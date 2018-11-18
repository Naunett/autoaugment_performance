from keras import models, layers, backend, initializers
import tensorflow as tf
from transformations import get_transformations
import PIL.Image
import numpy as np

LSTM_UNITS = 100

SUBPOLICIES = 5
SUBPOLICY_OPS = 2

OP_TYPES = 16
OP_PROBS = 11
OP_MAGNITUDES = 10


class Operation:
    def __init__(self, X, types_softmax, probs_softmax, magnitudes_softmax, argmax=False):
        # Ekin Dogus says he sampled the softmaxes, and has not used argmax
        # We might still want to use argmax=True for the last predictions, to ensure
        # the best solutions are chosen and make it deterministic.
        transformations = get_transformations(X)
        if argmax:
            self.type = types_softmax.argmax()
            t = transformations[self.type]
            self.prob = probs_softmax.argmax() / (OP_PROBS-1)
            m = magnitudes_softmax.argmax() / (OP_MAGNITUDES-1)
            self.magnitude = m*(t[2]-t[1]) + t[1]
        else:
            self.type = np.random.choice(OP_TYPES, p=types_softmax)
            t = transformations[self.type]
            self.prob = np.random.choice(
                np.linspace(0, 1, OP_PROBS), p=probs_softmax)
            self.magnitude = np.random.choice(np.linspace(
                t[1], t[2], OP_MAGNITUDES), p=magnitudes_softmax)
        self.transformation = t[0]

    def __call__(self, X):
        _X = []
        for x in X:
            if np.random.rand() < self.prob:
                x = PIL.Image.fromarray(x)
                x = self.transformation(x, self.magnitude)
            _X.append(np.array(x))
        return np.array(_X)

    def __str__(self):
        return 'Operation %2d (P=%.3f, M=%.3f)' % (self.type, self.prob, self.magnitude)


class Subpolicy:
    def __init__(self, *operations):
        self.operations = operations

    def __call__(self, X):
        for op in self.operations:
            X = op(X)
        return X

    def __str__(self):
        ret = ''
        for i, op in enumerate(self.operations):
            ret += str(op)
            if i < len(self.operations)-1:
                ret += '\n'
        return ret


class Controller:
    def __init__(self):
        self.model = self.create_model()
        self.scale = tf.placeholder(tf.float32, ())
        self.grads = tf.gradients(
            self.model.outputs, self.model.trainable_weights)
        # negative for gradient ascent
        self.grads = [g * (-self.scale) for g in self.grads]
        self.grads = zip(self.grads, self.model.trainable_weights)
        self.optimizer = tf.train.GradientDescentOptimizer(
            0.00035).apply_gradients(self.grads)

    def create_model(self):
        # Implementation note: Keras requires an input. I create an input and then feed
        # zeros to the network. Ugly, but it's the same as disabling those weights.
        # Furthermore, Keras LSTM input=output, so we cannot produce more than SUBPOLICIES
        # outputs. This is not desirable, since the paper produces 25 subpolicies in the
        # end.
        input_layer = layers.Input(shape=(SUBPOLICIES, 1))
        init = initializers.RandomUniform(-0.1, 0.1)
        lstm_layer = layers.LSTM(
            LSTM_UNITS, recurrent_initializer=init, return_sequences=True,
            name='controller')(input_layer)
        outputs = []
        for i in range(SUBPOLICY_OPS):
            name = 'op%d-' % (i+1)
            outputs += [
                layers.Dense(OP_TYPES, activation='softmax',
                             name=name + 't')(lstm_layer),
                layers.Dense(OP_PROBS, activation='softmax',
                             name=name + 'p')(lstm_layer),
                layers.Dense(OP_MAGNITUDES, activation='softmax',
                             name=name + 'm')(lstm_layer),
            ]
        return models.Model(input_layer, outputs)

    def fit(self, mem_softmaxes, mem_accuracies):
        session = backend.get_session()
        min_acc = np.min(mem_accuracies)
        max_acc = np.max(mem_accuracies)
        dummy_input = np.zeros((1, SUBPOLICIES, 1))
        dict_input = {self.model.input: dummy_input}
        # FIXME: the paper does mini-batches (10)
        for softmaxes, acc in zip(mem_softmaxes, mem_accuracies):
            scale = (acc-min_acc) / (max_acc-min_acc)
            dict_outputs = {_output: s for _output,
                            s in zip(self.model.outputs, softmaxes)}
            dict_scales = {self.scale: scale}
            # session.run(self.optimizer, feed_dict={
            #            **dict_outputs, **dict_scales, **dict_input})
        return self

    def predict(self, size, X):
        subpolicies = []
        for i in range(SUBPOLICIES):
            operations = []
            for j in range(SUBPOLICY_OPS):
                types_softmax = np.random.uniform(size=OP_TYPES)
                types_softmax = types_softmax / types_softmax.sum()
                probs_softmax = np.random.uniform(size=OP_PROBS)
                probs_softmax = probs_softmax / probs_softmax.sum()
                magnitudes_softmax = np.random.uniform(size=OP_MAGNITUDES)
                magnitudes_softmax = magnitudes_softmax / magnitudes_softmax.sum()
                ##print(types_softmax, probs_softmax, magnitudes_softmax)
                operation = Operation(
                    X, types_softmax, probs_softmax, magnitudes_softmax, argmax=False)
                operations.append(operation)
            subpolicies.append(Subpolicy(*operations))
        softmaxes = 0
        return softmaxes, subpolicies

# generator


def autoaugment(subpolicies, X, y, child_batch_size):
    while True:
        ix = np.arange(len(X))
        np.random.shuffle(ix)
        for i in range(len(X) // child_batch_size):
            _ix = ix[i*child_batch_size:(i+1)*child_batch_size]
            _X = X[_ix]
            _y = y[_ix]
            subpolicy = np.random.choice(subpolicies)
            _X = subpolicy(_X)
            _X = _X.astype(np.float32) / 255
            yield _X, _y
