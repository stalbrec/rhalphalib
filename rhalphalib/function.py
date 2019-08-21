import numpy as np
from scipy.special import binom
import numbers
from .parameter import IndependentParameter, NuisanceParameter
from .util import install_roofit_helpers


class BernsteinPoly(object):
    def __init__(self, name, order, dim_names=None, init_params=None, limits=None):
        '''
        Construct a multidimensional Bernstein polynomial
            name: will be used to prefix any RooFit object names
            order: tuple of order in each dimension
            dim_names: optional, names of each dimension
            initial_params: ndarray of initial params
            limits: tuple of independent parameter limits, default: (0, 10)
        '''
        self._name = name
        if not isinstance(order, tuple):
            raise ValueError
        self._order = order
        self._shape = tuple(n + 1 for n in order)
        if dim_names:
            if len(order) != len(dim_names):
                raise ValueError
            self._dim_names = dim_names
        else:
            self._dim_names = ['dim%d' % i for i in range(len(self._order))]
        if isinstance(init_params, np.ndarray):
            if init_params.shape != self._shape:
                raise ValueError
            self._init_params = init_params
        elif init_params is None:
            self._init_params = np.ones(shape=self._shape)
        else:
            raise ValueError
        if limits is None:
            limits = (0., 10.)

        # Construct Bernstein matrix for each dimension
        self._bmatrices = []
        for idim, (n, initialv) in enumerate(zip(self._order, self._init_params)):
            v = np.arange(n + 1)
            bmat = np.einsum("l,lv,lv->vl", binom.outer(n, v), binom.outer(v, v), np.power(-1., np.subtract.outer(v, v)))
            bmat[np.greater.outer(v, v)] = 0  # v > l
            self._bmatrices.append(bmat)

        # Construct parameter tensor
        self._params = np.full(self._shape, None)
        for ipar, initial in np.ndenumerate(self._init_params):
            param = IndependentParameter('_'.join([self.name] + ['%s_par%d' % (d, i) for d, i in zip(self._dim_names, ipar)]), initial, lo=limits[0], hi=limits[1])
            self._params[ipar] = param

    @property
    def name(self):
        return self._name

    @property
    def parameters(self):
        return self._params

    @parameters.setter
    def parameters(self, newparams):
        if not isinstance(newparams, np.ndarray):
            raise ValueError("newparams should be numpy array")
        elif newparams.shape != self._params.shape:
            raise ValueError("newparams shape does not match")
        for pnew, pold in zip(newparams.reshape(-1), self._params.reshape(-1)):
            pnew.name = pold.name
            # probably worth caching
            if pnew.intermediate:
                pnew.intermediate = False
        self._params = newparams

    def coefficients(self, *xvals):
        # evaluate Bernstein polynomial product tensor
        bpolyval = np.ones_like(xvals[0])
        for x, n, B in zip(xvals, self._order, self._bmatrices):
            xpow = np.power.outer(x, np.arange(n + 1))
            bpolyval = np.einsum("vl,xl,x...->x...v", B, xpow, bpolyval)

        return bpolyval

    def __call__(self, *vals):
        if len(vals) != len(self._order):
            raise ValueError("Not all dimension values specified")
        xvals = []
        shape = None
        for x in vals:
            if isinstance(x, numbers.Number):
                x = np.array(x)
            if not np.all((x >= 0) & (x <= 1)):
                raise ValueError("Bernstein polynomials are only defined on the interval [0, 1]")
            if shape is None:
                shape = x.shape
            elif shape != x.shape:
                raise ValueError("BernsteinPoly: all variables must have same shape")
            xvals.append(x.flatten())

        parameters = self._params.reshape(-1)
        coefficients = self.coefficients(*xvals).reshape(-1, parameters.size)

        out = np.full(coefficients.shape[0], None)
        for i in range(coefficients.shape[0]):
            # sum small coefficients first
            order = np.argsort(coefficients[i])
            p = np.sum(parameters[order]*coefficients[i][order])
            dimstr = '_'.join('%s%.3f' % (d, v[i]) for d, v in zip(self._dim_names, xvals))
            p.name = self.name + '_eval_' + dimstr.replace('.', 'p')
            p.intermediate = False
            out[i] = p
        return out.reshape(shape)


class DecorrelatedNuisanceVector(object):
    def __init__(self, prefix, param_in, param_cov):
        if not isinstance(param_in, np.ndarray):
            raise ValueError("Expecting param_in to be numpy array")
        if not isinstance(param_cov, np.ndarray):
            raise ValueError("Expecting param_cov to be numpy array")
        if not (len(param_in.shape) == 1
                and len(param_cov.shape) == 2
                and param_cov.shape[0] == param_in.shape[0]
                and param_cov.shape[1] == param_in.shape[0]):
            raise ValueError("param_in and param_cov have mismatched shapes")

        _, s, v = np.linalg.svd(param_cov)
        self._transform = np.sqrt(s)[:, None] * v
        self._parameters = np.array([NuisanceParameter(prefix + str(i), 'param') for i in range(param_in.size)])
        self._correlated = np.full(self._parameters.shape, None)
        for i in range(self._parameters.size):
            coef = self._transform[:, i]
            order = np.argsort(np.abs(coef))
            self._correlated[i] = np.sum(self._parameters[order]*coef[order]) + param_in[i]

    @classmethod
    def fromRooFitResult(cls, prefix, fitresult, param_names=None):
        install_roofit_helpers()
        names = [p.GetName() for p in fitresult.floatParsFinal()]
        means = fitresult.valueArray()
        cov = fitresult.covarianceArray()
        if param_names is not None:
            pidx = np.array([names.index(pname) for pname in param_names])
            means = means[pidx]
            cov = cov[np.ix_(pidx, pidx)]
        out = cls(prefix, means, cov)
        if param_names is not None:
            for p, name in zip(out.correlated_params, param_names):
                p.name = name
        return out

    @property
    def parameters(self):
        return self._parameters

    @property
    def correlated_params(self):
        return self._correlated
