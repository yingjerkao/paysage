import numpy
from .. import layers
from ..backends import numba_engine as en
from ..models.initialize import init_hidden as init
from .. import constraints
from .. import penalties


#---- MODEL CLASSES ----#

class LatentModel(object):
    """LatentModel
       Abstract class for a 2-layer neural network.
       
    """
    def __init__(self):
        self.layers = {}
        self.params = {}
        self.constraints = {}
        self.penalty = {}
                
    # placeholder function -- defined in each layer
    def sample_hidden(self, visible):
        pass
    
    # placeholder function -- defined in each layer
    def sample_visible(self, hidden):
        pass        
    
    # placeholder function -- defined in each layer
    def marginal_energy(self, visible):
        pass
    
    def add_constraints(self, cons):
        for key in cons:
            assert key in self.params
            self.constraints[key] = cons[key]
    
    def enforce_constraints(self):
        for key in self.constraints:
            getattr(constraints, self.constraints[key])(self.params[key])
            
    def add_weight_decay(self, penalty, method='l2_penalty'):
        self.penalty = {'weights': getattr(penalties, method)(penalty)}
    
    def resample_state(self, visibile, temperature=1.0):
        energies = self.marginal_energy(visibile)
        weights = en.importance_weights(energies, numpy.float32(temperature)).clip(min=0.0)
        indices = numpy.random.choice(numpy.arange(len(visibile)), size=len(visibile), replace=True, p=weights)
        return visibile[list(indices)]  
    
    def mcstep(self, vis):
        """gibbs_step(v):
           v -> h -> v'
           return v'
        
        """
        hid = self.sample_hidden(vis)
        return self.sample_visible(hid)
        
    def markov_chain(self, vis, steps, resample=False, temperature=1.0):
        """gibbs_chain(v, n):
           v -> h -> v_1 -> h_1 -> ... -> v_n
           return v_n
            
        """
        new_vis = vis.astype(vis.dtype)
        for t in range(steps):
            new_vis = self.mcstep(new_vis)
            if resample:
                new_vis = self.resample_state(new_vis, temperature=temperature)
        return new_vis
        
    def mean_field_step(self, vis):
        """mean_field_step(v):
           v -> h -> v'
           return v'
        
           It may be worth looking into extended approaches:
           Gabrié, Marylou, Eric W. Tramel, and Florent Krzakala. "Training Restricted Boltzmann Machine via the￼ Thouless-Anderson-Palmer free energy." Advances in Neural Information Processing Systems. 2015.
        
        """
        hid = self.hidden_mean(vis)
        return self.visible_mean(hid)   
        
    def mean_field_iteration(self, vis, steps):
        """mean_field_iteration(v, n):
           v -> h -> v_1 -> h_1 -> ... -> v_n
           return v_n
            
        """
        new_vis = vis.astype(vis.dtype)
        for t in range(steps):
            new_vis = self.mean_field_step(new_vis)
        return new_vis
        
    def deterministic_step(self, vis):
        """deterministic_step(v):
           v -> h -> v'
           return v'
        
        """
        hid = self.hidden_mode(vis)
        return self.visible_mode(hid)   
        
    def deterministic_iteration(self, vis, steps):
        """mean_field_iteration(v, n):
           v -> h -> v_1 -> h_1 -> ... -> v_n
           return v_n
            
        """
        new_vis = vis.astype(vis.dtype)
        for t in range(steps):
            new_vis = self.deterministic_step(new_vis)
        return new_vis
        
    def random(self, visible):
        return self.layers['visible'].random(visible)
        
   
class RestrictedBoltzmannMachine(LatentModel):
    """RestrictedBoltzmanMachine
    
       Hinton, Geoffrey. "A practical guide to training restricted Boltzmann machines." Momentum 9.1 (2010): 926.
    
    """
    def __init__(self, nvis, nhid, vis_type='ising', hid_type='bernoulli'):
        assert vis_type in ['ising', 'bernoulli']
        assert hid_type in ['ising', 'bernoulli']
        
        super().__init__()
        
        self.nvis = nvis
        self.nhid = nhid
        
        self.layers['visible'] = layers.get(vis_type)
        self.layers['hidden'] = layers.get(hid_type)
                
        self.params['weights'] = numpy.random.normal(loc=0.0, scale=0.01, size=(nvis, nhid)).astype(dtype=numpy.float32)
        self.params['visible_bias'] = numpy.zeros(nvis, dtype=numpy.float32)  
        self.params['hidden_bias'] = numpy.zeros(nhid, dtype=numpy.float32) 
        
    def initialize(self, data, method='hinton'):
        try:
            func = getattr(init, method)
        except AttributeError:
            print('{} is not a valid initialization method for latent models'.format(method))
        func(data, self)
        self.enforce_constraints()

    def hidden_field(self, visible):
        return self.params['hidden_bias'] + numpy.dot(visible, self.params['weights'])

    def visible_field(self, hidden):
        return self.params['visible_bias'] + numpy.dot(hidden, self.params['weights'].T)
        
    def sample_hidden(self, visible):
        return self.layers['hidden'].sample_state(self.hidden_field(visible))
            
    def hidden_mean(self, visible):
        return self.layers['hidden'].mean(self.hidden_field(visible))
            
    def hidden_mode(self, visible):
        return self.layers['hidden'].prox(self.hidden_field(visible))
              
    def sample_visible(self, hidden):
        return self.layers['visible'].sample_state(self.visible_field(hidden))
            
    def visible_mean(self, hidden):
        return self.layers['visible'].mean(self.visible_field(hidden))
            
    def visible_mode(self, hidden):
        return self.layers['visible'].prox(self.visible_field(hidden))
        
    def joint_energy(self, visible, hidden):
        energy = -numpy.dot(visible, self.params['visible_bias']) - numpy.dot(hidden, self.params['hidden_bias'])
        if len(visible.shape) == 2:
            energy = energy - en.batch_dot(visible.astype(numpy.float32), self.params['weights'], hidden.astype(numpy.float32))
        else:
            energy =  energy - numpy.dot(visible, numpy.dot(self.params['weights'], hidden))
        return numpy.mean(energy)
   
    def marginal_free_energy(self, visible):
        log_Z_hidden = self.layers['hidden'].log_partition_function(self.hidden_field(visible))
        return -numpy.dot(visible, self.params['visible_bias']) - numpy.sum(log_Z_hidden)

    def derivatives(self, visible):
        mean_hidden = self.hidden_mean(visible)
        derivs = {}
        if len(mean_hidden.shape) == 2:
            derivs['visible_bias'] = -numpy.mean(visible, axis=0)
            derivs['hidden_bias'] = -numpy.mean(mean_hidden, axis=0)
            derivs['weights'] = -en.batch_outer(visible, mean_hidden)
        else:
            derivs['visible_bias'] = -visible
            derivs['hidden_bias'] = -mean_hidden
            derivs['weights'] = -en.outer(visible, mean_hidden)
        return derivs


#TODO:
class HopfieldModel(LatentModel):
    """HopfieldModel
       A model of associative memory with binary visible units and Gaussian hidden units.        
       
       Hopfield, John J. "Neural networks and physical systems with emergent collective computational abilities." Proceedings of the national academy of sciences 79.8 (1982): 2554-2558.
    
    """    
    def __init__(self, nvis, nhid):
        pass

#TODO:
class HookeMachine(LatentModel):
    """HookeMachine
    
       Unpublished. Charles K. Fisher (2016)
    
    """
    def __init__(self, nvis, nhid, vis_type='gauss', hid_type='expo'):   
        pass


    
# ----- ALIASES ----- #
    
RBM = RestrictedBoltzmannMachine    
            