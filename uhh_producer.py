import rhalphalib as rl
import numpy as np
import sys
from ROOT import TFile, TH1F
def uhh_producer(configs=None):
    if('ModelName' not in configs):
        ModelName='UHH_Model'
    else:
        ModelName=configs['ModelName']

    gridHistFileName=configs['gridHistFileName']

    print('reading grid for nuisance parameter:')
    gridHistFile=TFile(gridHistFileName,'READ')
    gridHist=gridHistFile.Get('grid')
    gridAxises=dict(item.strip().split("=") for item in gridHist.GetTitle().split(","))
    xBins=range(gridHist.GetNbinsX())
    yBins=range(gridHist.GetNbinsY())

    categoriesHist=gridHistFile.Get('categories')
    ParticleCategories=[]
    for i in range(1,categoriesHist.GetNbinsX()+1):
        ParticleCategories.append(categoriesHist.GetXaxis().GetBinLabel(i))

    gridHistFile.Close()
    print('used variation categories:',ParticleCategories)
    print('X: %s , %i bins'%(gridAxises['x'],len(xBins)))
    print('Y: %s , %i bins'%(gridAxises['y'],len(yBins)))


    model = rl.Model(ModelName)
    gridNuisances=[]
    print('adding nuisance parameters:')
    for category in ParticleCategories:
        for i in xBins:
            for j in yBins:
                print('massScale_%s%i_%s%i_%s'%(gridAxises['x'],i,gridAxises['y'],j,category),'shape')
                gridNuisances.append([rl.NuisanceParameter('massScale_%s%i_%s%i_%s'%(gridAxises['x'],i,gridAxises['y'],j,category),'shape'),i,j,category])


    print('CMS_lumi', 'lnN')
    lumi = rl.NuisanceParameter('CMS_lumi', 'lnN')

    #########
    #QCD ESTIMATION
    ptbins = np.array([500, 550, 600, 675, 800, 1200])
    npt = len(ptbins) - 1
    msdbins = np.linspace(50, 170, 21)
    nmsd = len(msdbins) - 1

    tf = rl.BernsteinPoly("qcd_pass_ralhpTF",(3,3),['pt','rho'])
    # here we derive these all at once with 2D array
    ptpts, msdpts = np.meshgrid(ptbins[:-1] + 0.3 * np.diff(ptbins), msdbins[:-1] + 0.3 * np.diff(msdbins), indexing='ij')
    rhopts = 2*np.log(msdpts/ptpts)
    ptscaled = (ptpts - 450.) / (1200. - 450.)
    rhoscaled = (rhopts - (-6)) / ((-2.1) - (-6))
    validbins = (rhoscaled >= 0) & (rhoscaled <= 1)
    rhoscaled[~validbins] = 1  # we will mask these out later
    tf_params = tf(ptscaled, rhoscaled)
    #########

    channels=configs['channels']

    if(channels == None):
        print('must specify channel Configurations!')
        exit(0)

    for channelName,config in channels.items():
        print(channelName)
        RebinMSD='W' in channelName
        # RebinMSD=True
        histLocation=config['histLocation']
        if('variable' in config):
            Variable=config['variable']
        else:
            Variable='Mass'
        Samples=config['samples']

        if ('regions' not in config) or (len(config['regions'])==0):
            config['regions']=['']

        for region in config['regions']:
            region_suffix= ('_'+region) if len(region) > 0 else ''
            histDir=config['histDir']+ region_suffix
            regionName=channelName+region
            ch=rl.Channel(regionName)
            model.addChannel(ch)
            for sName in Samples:
                #don't include QCD Templates, but instead use rhalphabet method for QCD Estimation
                if('QCD' in sName):
                    continue
                sampleType=rl.Sample.SIGNAL if sName==config['signal'] else rl.Sample.BACKGROUND
                sFile=TFile('%s/%s.root'%(histLocation,sName),'READ')
                hist=sFile.Get(histDir+'/'+Variable+'_central')
                if(RebinMSD):
                    hist=hist.Rebin(len(msdbins)-1,hist.GetName()+'_newBinning',msdbins)
                sample=rl.TemplateSample(ch.name+'_'+sName,sampleType,hist)
                for (gridNuisance,x,y,category) in gridNuisances:
                    histUp=sFile.Get('%s/%s_%i_%i_%s_up'%(histDir,Variable,x,y,category))
                    histDown=sFile.Get('%s/%s_%i_%i_%s_down'%(histDir,Variable,x,y,category))
                    if(RebinMSD):
                        histUp=histUp.Rebin(len(msdbins)-1,histUp.GetName()+'_newBinning',msdbins)
                        histDown=histDown.Rebin(len(msdbins)-1,histDown.GetName()+'_newBinning',msdbins)
                    sample.setParamEffect(gridNuisance,histUp,histDown)
                    sample.setParamEffect(lumi, 1.027)
                ch.addSample(sample)
            dataFile=TFile('%s/%s.root'%(histLocation,config['obs']))

            if 'varyPseudoLike' in config and config['obs']=="Pseudo":
                histPath=config['varyPseudoLike']
                if '/' not in histPath:
                    histPath=histDir+'/'+histPath
                dataHist=dataFile.Get(histPath)
                dataHist.SetName('Mass_central')
            else:
                #necessary for 2016 JetHT UHH Ntuples because they are missing JetConstituents
                if('W' in channelName and config['obs']=="Data"):
                    dataHistDir=histDir.split('pt')[0]+'noConst_pt'+histDir.split('pt')[1]
                else:
                    dataHistDir=histDir
                dataHist=dataFile.Get(dataHistDir+'/'+'Mass_central')
            if(RebinMSD):
                dataHist=dataHist.Rebin(len(msdbins)-1,dataHist.GetName()+'_newBinning',msdbins)

            ch.setObservation(dataHist)

        #Go to next Sample if QCD is not to be included:
        if 'QCD' not in Samples:
            continue
        # steal observable definition from fail channel
        ptbin=np.where(ptbins==float(channelName.split('Pt')[-1]))[0][0]
        failCh = model[channelName+'fail']
        obs = failCh.observable
        print('channelName',channelName)
        qcdparams = np.array([rl.IndependentParameter('qcdparam_%s_msdbin%d' % (channelName, i), 0) for i in range(nmsd)])
        initial_qcd = failCh.getObservation().astype(float)  # was integer, and numpy complained about subtracting float from it
        for sample in failCh:
            initial_qcd -= sample.getExpectation(nominal=True)
        if np.any(initial_qcd < 0.):
            raise ValueError("uh-oh")
        scaledparams = initial_qcd  + 2.*np.sqrt(initial_qcd)*qcdparams
        fail_qcd = rl.ParametericSample('%sfail_qcd' % channelName, rl.Sample.BACKGROUND, obs, scaledparams)
        failCh.addSample(fail_qcd)
        pass_qcd = rl.TransferFactorSample('%spass_qcd' % channelName, rl.Sample.BACKGROUND, tf_params[ptbin, :], fail_qcd)
        model[channelName+'pass'].addSample(pass_qcd)

    model.renderCombine(ModelName)

if __name__ == '__main__':
    import json
    #load configuration from JSON-File
    configs=json.load(open(sys.argv[1]))
    #create datacards and workspace
    uhh_producer(configs)

    #run Fit in new environment
    from runFit import runFits
    if('pathCMSSW' in configs):
        runFits([configs['ModelName']],configs['pathCMSSW'])
    else:
        runFits([configs['ModelName']])
