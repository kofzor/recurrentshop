from keras.layers import Layer, InputSpec
from keras.models import Sequential
from keras import initializations, regularizers
from keras import backend as K
from inspect import getargspec
import numpy as np


'''Provides a simpler API for building complex recurrent neural networks using Keras.

The RNN logic is written inside RNNCells, which are added sequentially to a RecurrentContainer.
A RecurrentContainer behaves similar to a Recurrent layer in Keras, and accepts arguments like 
return_sequences, unroll, stateful, etc [See Keras Recurrent docstring]
The .add() method of a RecurrentContainer is used to add RNNCells and other layers to it. Each 
element in the input sequence passes through the layers in the RecurrentContainer in the order
in which they were added.
'''


__author__ = "Fariz Rahman"
__copyright__ = "Copyright 2016, datalog.ai"
__credits__ = ["Fariz Rahman", "Malaikannan Sankarasubbu"]
__license__ = "GPL"
__version__ = "0.0.1"
__maintainer__ = "Fariz Rahman"
__email__ = "fariz@datalog.ai"
__status__ = "Production"


def _isRNN(layer):
	return issubclass(layer.__class__, RNNCell)


class weight(object):

	def __init__(self, value, init='glorot_uniform', regularizer=None, trainable=True, name=None):
		if type(value) == int:
			value = (value,)
		if type(value) in [tuple, list]:
			if type(init) == str:
				init = initializations.get(init, name=name)
			self.value = init(value)
		elif 'numpy' in str(type(value)):
			self.value = K.variable(value, name=name)
		else:
			self.value = value
		if type(regularizer) == str:
			regularizer = regularizers.get(regularizer)
		self.regularizer = regularizer
		self.trainable = trainable


class RNNCell(Layer):

	def __init__(self, **kwargs):
		if 'input_dim' in kwargs:
			kwargs['input_shape'] = (kwargs['input_dim'],)
			del kwargs['input_dim']
		super(RNNCell, self).__init__(**kwargs)

	def _step(self, x, states):
		args = [x, states]
		if hasattr(self, 'weights'):
			args += [self.weights]
		if hasattr(self, 'constants'):
			args += [self.constants]
		args = args[:len(getargspec(self.step).args)]
		return self.step(*args)

	def build(self, input_shape):
		self.input_spec = [InputSpec(shape=input_shape)]
		super(RNNCell, self).build(input_shape)

	@property
	def weights(self):
		w = []
		if hasattr(self, 'trainable_weights'):
			w += self.trainable_weights
		if hasattr(self, 'non_trainable_weights'):
			w += self.non_trainable_weights
		return w

	@weights.setter
	def weights(self, ws):
		self.trainable_weights = []
		self.non_trainable_weights = []
		self.regularizers = []
		for w in ws:
			if not isinstance(w, weight):
				w = weight(w)
			if w.trainable:
				self.trainable_weights += [w.value]
			else:
				self.non_trainable_weights += [w.value]
			if w.regularizer:
				w.regularizer.set_param(w.value)
				self.regularizers += [w.regularizer]

	def get_output_shape_for(self, input_shape):
		if hasattr(self, 'output_dim'):
			return input_shape[:-1] + (self.output_dim,)
		else:
			return input_shape


class RecurrentContainer(Layer):

	def __init__(self, weights=None, return_sequences=False, go_backwards=False, stateful=False, input_length=None, unroll=False):
		self.return_sequences = return_sequences
		self.initial_weights = weights
		self.go_backwards = go_backwards
		self.stateful = stateful
		self.input_length = input_length
		self.unroll = unroll
		self.supports_masking = True
		self.model = Sequential()
		super(RecurrentContainer, self).__init__()

	def add(self, layer):
		'''Add a layer
		# Arguments:
		layer: Layer instance. RNNCell or a normal layer such as Dense.
		'''
		self.model.add(layer)
		if len(self.model.layers) == 1:
			shape = layer.input_spec[0].shape
			shape = (shape[0], self.input_length) + shape[1:]
			self.batch_input_shape = shape
			self.input_spec = [InputSpec(shape=shape)]
		if self.stateful:
			self.reset_states()

	def pop(self):
		'''Remove the last layer
		'''
		self.model.pop()
		if self.stateful:
			self.reset_states()
	
	@property
	def input_shape(self):
		return self.input_spec[0].shape

	@property
	def output_shape(self):
		input_length = self.input_spec[0].shape[1]
		shape = self.model.output_shape
		if self.return_sequences:
			return (shape[0], input_length) + shape[1:]
		else:
			return shape

	def get_output_shape_for(self, input_shape):
		return self.output_shape

	def step(self, x, states):
		states = list(states)
		state_index = 0
		nb_states = []
		for layer in self.model.layers:
			if _isRNN(layer):
				x, new_states = layer._step(x, states[state_index : state_index + len(layer.states)])
				states[state_index : state_index + len(layer.states)] = new_states
				state_index += len(layer.states)
			else:
				x = layer.call(x)
		return x, states
	
	def call(self, x, mask=None):
		input_shape = self.input_spec[0].shape
		if self.stateful:
			initial_states = self.states
		else:
			initial_states = self.get_initial_states(x)
		last_output, outputs, states = K.rnn(self.step, x, initial_states, go_backwards=self.go_backwards, mask=mask, unroll=self.unroll, input_length=input_shape[1])
		if self.stateful:
			self.updates = []
			for i in range(len(states)):
				self.updates.append((self.states[i], states[i]))
		if self.return_sequences:
			return outputs
		else:
			return last_output

	def get_initial_states(self, x):
		initial_states = []
		batch_size = self.input_spec[0].shape[0]
		input_length = self.input_spec[0].shape[1]
		if input_length is None:
			input_length = K.shape(x)[1]
		if batch_size is None:
			batch_size = K.shape(x)[0]
		input = self._get_first_timestep(x)
		for layer in self.model.layers:
			if _isRNN(layer):
				layer_initial_states = []
				for state in layer.states:
					state = self._get_state_from_info(state, input, batch_size, input_length)
					if type(state) != list:
						state = [state]
					layer_initial_states += state
				initial_states += layer_initial_states
				input = layer._step(input, layer_initial_states)[0]
			else:
				input = layer.call(input)
		return initial_states

	def reset_states(self):
		batch_size = self.input_spec[0].shape[0]
		input_length = self.input_spec[0].shape[1]
		states = []
		for layer in self.model.layers:
			if _isRNN(layer):
				for state in layer.states:
					assert type(state) in [tuple, list] or 'numpy' in str(type(state)), 'Stateful RNNs require states with static shapes'
					if 'numpy' in str(type(state)):
						states += [K.variable(state)]
					else:
						state = list(state)
						for i in range(len(state)):
							if state[i] in [-1, 'batch_size']:
								assert type(batch_size) == int, 'Stateful RNNs require states with static shapes'
								state[i] = batch_size
							elif state[i] == 'input_length':
								assert type(input_length) == int, 'Stateful RNNs require states with static shapes'
								state[i] = input_length
						states += [K.variable(np.zeros(state))]
		self.states = states

	def _get_state_from_info(self, info, input, batch_size, input_length):
		if hasattr(info, '__call__'):
			return info(input)
		elif type(info) is tuple:
			info = list(info)
			for i in range(len(info)):
				if info[i] in [-1, 'batch_size']:
					info[i] = batch_size
				elif info[i] == 'input_length':
					info[i] = input_length
			if K._BACKEND == 'theano':
				from theano import tensor as k
			else:
				import tensorflow as k
			return k.zeros(info)
		elif 'numpy' in str(type(info)):
			return K.variable(info)
		else:
			return info

	def _get_first_timestep(self, x):
		slices = [slice(None)] * K.ndim(x)
		slices[1] = 0
		return x[slices]

	@property
	def trainable_weights(self):
		return self.model.trainable_weights

	@trainable_weights.setter
	def trainable_weights(self, value):
		pass

	@property
	def non_trainable_weights(self):
		return self.model.non_trainable_weights

	@non_trainable_weights.setter
	def non_trainable_weights(self, value):
		pass

	@property
	def weights(self):
		return self.model.weights

	@property
	def regularizers(self):
		return self.model.regularizers

	@regularizers.setter
	def regularizers(self, value):
		pass

	def get_config(self):
		
		attribs = ['return_sequences', 'go_backwards', 'stateful', 'input_length', 'unroll']
		config = {x : getattr(self, x) for x in attribs}
		config['model'] = self.model.get_config()
		base_config = super(RecurrentContainer, self).get_config()
		return dict(list(base_config.items()) + list(config.items()))

	@classmethod
	def from_config(cls, config):
		model_config = config['model']
		del config['model']
		rc = cls(**config)
		rc.model = Sequential.from_config(model_config)
		return rc
