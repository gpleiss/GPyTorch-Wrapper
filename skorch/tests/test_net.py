"""Tests for net.py"""

from functools import partial
import pickle
from unittest.mock import Mock
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import accuracy_score
from sklearn.model_selection import GridSearchCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.base import clone
import torch
from torch import nn
import torch.nn.functional as F

from skorch.utils import to_numpy
from skorch.utils import is_torch_data_type


torch.manual_seed(0)


class MyClassifier(nn.Module):
    """Simple classification module.

    We cannot use the module fixtures from conftest because they are
    not pickleable.

    """
    def __init__(self, num_units=10, nonlin=F.relu):
        super(MyClassifier, self).__init__()

        self.dense0 = nn.Linear(20, num_units)
        self.nonlin = nonlin
        self.dropout = nn.Dropout(0.5)
        self.dense1 = nn.Linear(num_units, 10)
        self.output = nn.Linear(10, 2)

    # pylint: disable=arguments-differ
    def forward(self, X):
        X = self.nonlin(self.dense0(X))
        X = self.dropout(X)
        X = self.nonlin(self.dense1(X))
        X = F.softmax(self.output(X), dim=-1)
        return X


# pylint: disable=too-many-public-methods
class TestNeuralNet:
    @pytest.fixture(scope='module')
    def data(self, classifier_data):
        return classifier_data

    @pytest.fixture(scope='module')
    def dummy_callback(self):
        from skorch.callbacks import Callback
        cb = Mock(spec=Callback)
        # make dummy behave like an estimator
        cb.get_params.return_value = {}
        cb.set_params = lambda **kwargs: cb
        return cb

    @pytest.fixture(scope='module')
    def module_cls(self):
        return MyClassifier

    @pytest.fixture(scope='module')
    def net_cls(self):
        from skorch.net import NeuralNetClassifier
        return NeuralNetClassifier

    @pytest.fixture
    def dataset_cls(self):
        from skorch.dataset import Dataset
        return Dataset

    @pytest.fixture(scope='module')
    def net(self, net_cls, module_cls, dummy_callback):
        return net_cls(
            module_cls,
            callbacks=[('dummy', dummy_callback)],
            max_epochs=10,
            lr=0.1,
        )

    @pytest.fixture(scope='module')
    def pipe(self, net):
        return Pipeline([
            ('scale', StandardScaler()),
            ('net', net),
        ])

    @pytest.fixture(scope='module')
    def net_fit(self, net, data):
        # Careful, don't call additional fits on this, since that would have
        # side effects on other tests.
        X, y = data
        return net.fit(X, y)

    @pytest.fixture
    def net_pickleable(self, net_fit):
        """NeuralNet instance that removes callbacks that are not
        pickleable.

        """
        # callback fixture not pickleable, remove it
        callbacks = net_fit.callbacks
        net_fit.callbacks = []
        callbacks_ = net_fit.callbacks_
        # remove mock callback
        net_fit.callbacks_ = [(n, cb) for n, cb in net_fit.callbacks_
                              if not isinstance(cb, Mock)]
        net_clone = clone(net_fit)
        net_fit.callbacks = callbacks
        net_fit.callbacks_ = callbacks_
        return net_clone

    def test_net_init_one_unknown_argument(self, net_cls, module_cls):
        with pytest.raises(TypeError) as e:
            net_cls(module_cls, unknown_arg=123)

        expected = ("__init__() got unexpected argument(s) unknown_arg."
                    "Either you made a typo, or you added new arguments "
                    "in a subclass; if that is the case, the subclass "
                    "should deal with the new arguments explicitely.")
        assert e.value.args[0] == expected

    def test_net_init_two_unknown_argument(self, net_cls, module_cls):
        with pytest.raises(TypeError) as e:
            net_cls(module_cls, lr=0.1, mxa_epochs=5,
                    warm_start=False, bathc_size=20)

        expected = ("__init__() got unexpected argument(s) "
                    "mxa_epochs, bathc_size."
                    "Either you made a typo, or you added new arguments "
                    "in a subclass; if that is the case, the subclass "
                    "should deal with the new arguments explicitely.")
        assert e.value.args[0] == expected

    def test_fit(self, net_fit):
        # fitting does not raise anything
        pass

    def test_net_learns(self, net_fit, data):
        X, y = data
        y_pred = net_fit.predict(X)
        assert accuracy_score(y, y_pred) > 0.65

    def test_forward(self, net_fit, data):
        X = data[0]
        n = len(X)
        y_forward = net_fit.forward(X)

        assert is_torch_data_type(y_forward)
        # Expecting (number of samples, number of output units)
        assert y_forward.shape == (n, 2)

        y_proba = net_fit.predict_proba(X)
        assert np.allclose(to_numpy(y_forward), y_proba)

    def test_forward_device_cpu(self, net_fit, data):
        X = data[0]

        # CPU by default
        y_forward = net_fit.forward(X)
        assert isinstance(X, np.ndarray)
        assert not y_forward.is_cuda

        y_forward = net_fit.forward(X, device='cpu')
        assert isinstance(X, np.ndarray)
        assert not y_forward.is_cuda

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
    def test_forward_device_gpu(self, net_fit, data):
        X = data[0]
        y_forward = net_fit.forward(X, device='cuda:0')
        assert isinstance(X, np.ndarray)
        assert y_forward.is_cuda

    def test_predict_and_predict_proba(self, net_fit, data):
        X = data[0]

        y_proba = net_fit.predict_proba(X)
        assert np.allclose(y_proba.sum(1), 1, rtol=1e-7)

        y_pred = net_fit.predict(X)
        assert np.allclose(np.argmax(y_proba, 1), y_pred, rtol=1e-7)

    def test_dropout(self, net_fit, data):
        # Note: does not test that dropout is really active during
        # training.
        X = data[0]

        # check that dropout not active by default
        y_proba = to_numpy(net_fit.forward(X))
        y_proba2 = to_numpy(net_fit.forward(X))
        assert np.allclose(y_proba, y_proba2, rtol=1e-7)

        # check that dropout can be activated
        y_proba = to_numpy(net_fit.forward(X, training=True))
        y_proba2 = to_numpy(net_fit.forward(X, training=True))
        assert not np.allclose(y_proba, y_proba2, rtol=1e-7)

    def test_pickle_save_load(self, net_pickleable, data, tmpdir):
        X, y = data
        score_before = accuracy_score(y, net_pickleable.predict(X))

        p = tmpdir.mkdir('skorch').join('testmodel.pkl')
        with open(str(p), 'wb') as f:
            pickle.dump(net_pickleable, f)
        del net_pickleable
        with open(str(p), 'rb') as f:
            net_new = pickle.load(f)

        score_after = accuracy_score(y, net_new.predict(X))
        assert np.isclose(score_after, score_before)

    @pytest.mark.parametrize('device', ['cpu', 'cuda'])
    def test_device_torch_device(self, net_cls, module_cls, device):
        # Check if native torch.device works as well.
        if device.startswith('cuda') and not torch.cuda.is_available():
            pytest.skip()
        net = net_cls(module=module_cls, device=torch.device(device))
        net = net.initialize()
        assert net.module_.dense0.weight.device.type.startswith(device)

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
    def test_pickle_save_load_cuda_intercompatibility(
            self, net_cls, module_cls, tmpdir):
        from skorch.exceptions import DeviceWarning

        net = net_cls(module=module_cls, device='cuda').initialize()

        p = tmpdir.mkdir('skorch').join('testmodel.pkl')
        with open(str(p), 'wb') as f:
            pickle.dump(net, f)
        del net

        with patch('torch.cuda.is_available', lambda *_: False):
            with pytest.warns(DeviceWarning) as w:
                with open(str(p), 'rb') as f:
                    m = pickle.load(f)

        # The loaded model should not use CUDA anymore as it
        # already knows CUDA is not available.
        assert m.device == 'cpu'

        assert len(w.list) == 1  # only 1 warning
        assert w.list[0].message.args[0] == (
            'Model configured to use CUDA but no CUDA '
            'devices available. Loading on CPU instead.')

    def test_pickle_save_and_load_uninitialized(
            self, net_cls, module_cls, tmpdir):
        net = net_cls(module_cls)
        p = tmpdir.mkdir('skorch').join('testmodel.pkl')
        with open(str(p), 'wb') as f:
            # does not raise
            pickle.dump(net, f)
        with open(str(p), 'rb') as f:
            pickle.load(f)

    def test_save_load_state_dict_file(
            self, net_cls, module_cls, net_fit, data, tmpdir):
        net = net_cls(module_cls).initialize()
        X, y = data

        score_before = accuracy_score(y, net_fit.predict(X))
        score_untrained = accuracy_score(y, net.predict(X))
        assert not np.isclose(score_before, score_untrained)

        p = tmpdir.mkdir('skorch').join('testmodel.pkl')
        with open(str(p), 'wb') as f:
            net_fit.save_params(f)
        del net_fit
        with open(str(p), 'rb') as f:
            net.load_params(f)

        score_after = accuracy_score(y, net.predict(X))
        assert np.isclose(score_after, score_before)

    def test_save_load_state_dict_str(
            self, net_cls, module_cls, net_fit, data, tmpdir):
        net = net_cls(module_cls).initialize()
        X, y = data

        score_before = accuracy_score(y, net_fit.predict(X))
        score_untrained = accuracy_score(y, net.predict(X))
        assert not np.isclose(score_before, score_untrained)

        p = tmpdir.mkdir('skorch').join('testmodel.pkl')
        net_fit.save_params(str(p))
        del net_fit
        net.load_params(str(p))

        score_after = accuracy_score(y, net.predict(X))
        assert np.isclose(score_after, score_before)

    def test_save_state_dict_not_init(
            self, net_cls, module_cls, tmpdir):
        from skorch.exceptions import NotInitializedError

        net = net_cls(module_cls)
        p = tmpdir.mkdir('skorch').join('testmodel.pkl')

        with pytest.raises(NotInitializedError) as exc:
            net.save_params(str(p))
        expected = ("Cannot save parameters of an un-initialized model. "
                    "Please initialize first by calling .initialize() "
                    "or by fitting the model with .fit(...).")
        assert exc.value.args[0] == expected

    def test_load_state_dict_not_init(
            self, net_cls, module_cls, tmpdir):
        from skorch.exceptions import NotInitializedError

        net = net_cls(module_cls)
        p = tmpdir.mkdir('skorch').join('testmodel.pkl')

        with pytest.raises(NotInitializedError) as exc:
            net.load_params(str(p))
        expected = ("Cannot load parameters of an un-initialized model. "
                    "Please initialize first by calling .initialize() "
                    "or by fitting the model with .fit(...).")
        assert exc.value.args[0] == expected

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
    def test_save_load_state_cuda_intercompatibility(
            self, net_cls, module_cls, tmpdir):
        net = net_cls(module_cls, device='cuda').initialize()

        p = tmpdir.mkdir('skorch').join('testmodel.pkl')
        net.save_params(str(p))

        with patch('torch.cuda.is_available', lambda *_: False):
            with pytest.warns(ResourceWarning) as w:
                net.load_params(str(p))

        assert w.list[0].message.args[0] == (
            'Model configured to use CUDA but no CUDA '
            'devices available. Loading on CPU instead.')

    def test_save_load_history_file_obj(
            self, net_cls, module_cls, net_fit, data, tmpdir):
        net = net_cls(module_cls).initialize()
        X, y = data

        history_before = net_fit.history

        p = tmpdir.mkdir('skorch').join('history.json')
        with open(str(p), 'w') as f:
            net_fit.save_history(f)
        del net_fit
        with open(str(p), 'r') as f:
            net.load_history(f)

        assert net.history == history_before

    @pytest.mark.parametrize('converter', [str, Path])
    def test_save_load_history_file_path(
            self, net_cls, module_cls, net_fit, data, tmpdir, converter):
        # Test loading/saving with different kinds of path representations.
        net = net_cls(module_cls).initialize()
        X, y = data

        history_before = net_fit.history

        p = tmpdir.mkdir('skorch').join('history.json')
        net_fit.save_history(converter(p))
        del net_fit
        net.load_history(converter(p))

        assert net.history == history_before

    @pytest.mark.parametrize('method, call_count', [
        ('on_train_begin', 1),
        ('on_train_end', 1),
        ('on_epoch_begin', 10),
        ('on_epoch_end', 10),
        # by default: 80/20 train/valid split
        ('on_batch_begin', (800 // 128 + 1) * 10 + (200 // 128 + 1) * 10),
        ('on_batch_end', (800 // 128 + 1) * 10 + (200 // 128 + 1) * 10),
    ])
    def test_callback_is_called(self, net_fit, method, call_count):
        # callback -2 is the mocked callback
        method = getattr(net_fit.callbacks_[-2][1], method)
        assert method.call_count == call_count
        assert method.call_args_list[0][0][0] is net_fit

    def test_history_correct_shape(self, net_fit):
        assert len(net_fit.history) == net_fit.max_epochs

    def test_history_default_keys(self, net_fit):
        expected_keys = {
            'train_loss', 'valid_loss', 'epoch', 'dur', 'batches', 'valid_acc'}
        for row in net_fit.history:
            assert expected_keys.issubset(row)

    def test_history_is_filled(self, net_fit):
        assert len(net_fit.history) == net_fit.max_epochs

    def test_set_params_works(self, net, data):
        X, y = data
        net.fit(X, y)

        assert net.module_.dense0.out_features == 10
        assert net.module_.dense1.in_features == 10
        assert net.module_.nonlin is F.relu
        assert np.isclose(net.lr, 0.1)

        net.set_params(
            module__num_units=20,
            module__nonlin=F.tanh,
            lr=0.2,
        )
        net.fit(X, y)

        assert net.module_.dense0.out_features == 20
        assert net.module_.dense1.in_features == 20
        assert net.module_.nonlin is F.tanh
        assert np.isclose(net.lr, 0.2)

    def test_set_params_then_initialize_remembers_param(
            self, net_cls, module_cls):
        net = net_cls(module_cls)

        # net does not 'forget' that params were set
        assert net.verbose != 123
        net.set_params(verbose=123)
        assert net.verbose == 123
        net.initialize()
        assert net.verbose == 123

    def test_set_params_on_callback_then_initialize_remembers_param(
            self, net_cls, module_cls):
        net = net_cls(module_cls).initialize()

        # net does not 'forget' that params were set
        assert dict(net.callbacks_)['print_log'].sink is print
        net.set_params(callbacks__print_log__sink=123)
        assert dict(net.callbacks_)['print_log'].sink == 123
        net.initialize()
        assert dict(net.callbacks_)['print_log'].sink == 123

    def test_changing_model_reinitializes_optimizer(self, net, data):
        # The idea is that we change the model using `set_params` to
        # add parameters. Since the optimizer depends on the model
        # parameters it needs to be reinitialized.
        X, y = data

        net.set_params(module__nonlin=F.relu)
        net.fit(X, y)

        net.set_params(module__nonlin=nn.PReLU())
        assert isinstance(net.module_.nonlin, nn.PReLU)
        d1 = net.module_.nonlin.weight.data.clone().cpu().numpy()

        # make sure that we do not initialize again by making sure that
        # the network is initialized and by using partial_fit.
        assert net.initialized_
        net.partial_fit(X, y)
        d2 = net.module_.nonlin.weight.data.clone().cpu().numpy()

        # all newly introduced parameters should have been trained (changed)
        # by the optimizer after 10 epochs.
        assert (abs(d2 - d1) > 1e-05).all()

    def test_setting_optimizer_needs_model(self, net_cls, module_cls):
        net = net_cls(module_cls)
        assert not hasattr(net, 'module_')
        # should not break
        net.set_params(optimizer=torch.optim.SGD)

    def test_optimizer_param_groups(self, net_cls, module_cls):
        net = net_cls(
            module_cls,
            optimizer__param_groups=[
                ('dense1.*', {'lr': 0.1}),
                ('dense*.*', {'lr': 0.5}),
            ],
        )
        net.initialize()

        # two custom (dense0, dense1), one default with the rest of the
        # parameters (output).
        assert len(net.optimizer_.param_groups) == 3
        assert net.optimizer_.param_groups[0]['lr'] == 0.1
        assert net.optimizer_.param_groups[1]['lr'] == 0.5
        assert net.optimizer_.param_groups[2]['lr'] == net.lr

    def test_module_params_in_init(self, net_cls, module_cls, data):
        X, y = data

        net = net_cls(
            module=module_cls,
            module__num_units=20,
            module__nonlin=F.tanh,
        )
        net.fit(X, y)

        assert net.module_.dense0.out_features == 20
        assert net.module_.dense1.in_features == 20
        assert net.module_.nonlin is F.tanh

    def test_module_initialized_with_partial_module(self, net_cls, module_cls):
        net = net_cls(partial(module_cls, num_units=123))
        net.initialize()
        assert net.module_.dense0.out_features == 123

    def test_criterion_init_with_params(self, net_cls, module_cls):
        mock = Mock()
        net = net_cls(module_cls, criterion=mock, criterion__spam='eggs')
        net.initialize()
        assert mock.call_count == 1
        assert mock.call_args_list[0][1]['spam'] == 'eggs'

    def test_criterion_set_params(self, net_cls, module_cls):
        mock = Mock()
        net = net_cls(module_cls, criterion=mock)
        net.initialize()
        net.set_params(criterion__spam='eggs')
        assert mock.call_count == 2
        assert mock.call_args_list[1][1]['spam'] == 'eggs'

    def test_callback_with_name_init_with_params(self, net_cls, module_cls):
        mock = Mock()
        net = net_cls(
            module_cls,
            criterion=Mock(),
            callbacks=[('cb0', mock)],
            callbacks__cb0__spam='eggs',
        )
        net.initialize()
        assert mock.initialize.call_count == 1
        assert mock.set_params.call_args_list[0][1]['spam'] == 'eggs'

    def test_callback_set_params(self, net_cls, module_cls):
        mock = Mock()
        net = net_cls(
            module_cls,
            criterion=Mock(),
            callbacks=[('cb0', mock)],
        )
        net.initialize()
        net.set_params(callbacks__cb0__spam='eggs')
        assert mock.initialize.call_count == 2  # callbacks are re-initialized
        assert mock.set_params.call_args_list[-1][1]['spam'] == 'eggs'

    def test_callback_name_collides_with_default(self, net_cls, module_cls):
        net = net_cls(module_cls, callbacks=[('train_loss', Mock())])

        with pytest.raises(ValueError) as exc:
            net.initialize()
        expected = "The callback name 'train_loss' appears more than once."
        assert str(exc.value) == expected

    def test_callback_same_name_twice(self, net_cls, module_cls):
        callbacks = [('cb0', Mock()),
                     ('cb1', Mock()),
                     ('cb0', Mock())]
        net = net_cls(module_cls, callbacks=callbacks)

        with pytest.raises(ValueError) as exc:
            net.initialize()
        expected = "The callback name 'cb0' appears more than once."
        assert str(exc.value) == expected

    def test_callback_same_inferred_name_twice(self, net_cls, module_cls):
        cb0 = Mock()
        cb1 = Mock()
        cb0.__class__.__name__ = 'some-name'
        cb1.__class__.__name__ = 'some-name'
        net = net_cls(module_cls, callbacks=[cb0, cb1])

        with pytest.raises(ValueError) as exc:
            net.initialize()
        expected = "The callback name 'some-name' appears more than once."
        assert str(exc.value) == expected

    def test_in_sklearn_pipeline(self, pipe, data):
        X, y = data
        pipe.fit(X, y)
        pipe.predict(X)
        pipe.predict_proba(X)
        pipe.set_params(net__module__num_units=20)

    def test_grid_search_works(self, net_cls, module_cls, data):
        net = net_cls(module_cls)
        X, y = data
        params = {
            'lr': [0.01, 0.02],
            'max_epochs': [10, 20],
            'module__num_units': [10, 20],
        }
        gs = GridSearchCV(net, params, refit=True, cv=3, scoring='accuracy')
        gs.fit(X[:100], y[:100])  # for speed
        print(gs.best_score_, gs.best_params_)

    def test_change_get_loss(self, net_cls, module_cls, data):
        from skorch.utils import to_tensor

        class MyNet(net_cls):
            # pylint: disable=unused-argument
            def get_loss(self, y_pred, y_true, X=None, training=False):
                y_true = to_tensor(y_true, device='cpu')
                loss_a = torch.abs(y_true.float() - y_pred[:, 1]).mean()
                loss_b = ((y_true.float() - y_pred[:, 1]) ** 2).mean()
                if training:
                    self.history.record_batch('loss_a', to_numpy(loss_a))
                    self.history.record_batch('loss_b', to_numpy(loss_b))
                return loss_a + loss_b

        X, y = data
        net = MyNet(module_cls, max_epochs=1)
        net.fit(X, y)

        diffs = []
        all_losses = net.history[
            -1, 'batches', :, ('train_loss', 'loss_a', 'loss_b')]
        diffs = [total - a - b for total, a, b in all_losses]
        assert np.allclose(diffs, 0, atol=1e-7)

    def test_net_no_valid(self, net_cls, module_cls, data):
        net = net_cls(
            module_cls,
            max_epochs=10,
            lr=0.1,
            train_split=None,
        )
        X, y = data
        net.fit(X, y)
        assert net.history[:, 'train_loss']
        with pytest.raises(KeyError):
            # pylint: disable=pointless-statement
            net.history[:, 'valid_loss']

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
    def test_use_cuda_on_model(self, net_cls, module_cls):
        net_cuda = net_cls(module_cls, device='cuda')
        net_cuda.initialize()
        net_cpu = net_cls(module_cls, device='cpu')
        net_cpu.initialize()

        cpu_tensor = net_cpu.module_.dense0.weight.data
        assert isinstance(cpu_tensor, torch.FloatTensor)

        gpu_tensor = net_cuda.module_.dense0.weight.data
        assert isinstance(gpu_tensor, torch.cuda.FloatTensor)

    def test_get_params_works(self, net_cls, module_cls):
        from skorch.callbacks import EpochScoring

        net = net_cls(
            module_cls, callbacks=[('myscore', EpochScoring('myscore'))])

        params = net.get_params(deep=True)
        # test a couple of expected parameters
        assert 'verbose' in params
        assert 'module' in params
        assert 'callbacks' in params
        assert 'callbacks__print_log__sink' in params
        # not yet initialized
        assert 'callbacks__myscore__scoring' not in params

        net.initialize()
        params = net.get_params(deep=True)
        # now initialized
        assert 'callbacks__myscore__scoring' in params

    @pytest.mark.xfail
    def test_get_params_with_uninit_callbacks(self, net_cls, module_cls):
        from skorch.callbacks import EpochTimer

        net = net_cls(
            module_cls,
            callbacks=[EpochTimer, ('other_timer', EpochTimer)],
        )
        # none of this raises an exception
        net = clone(net)
        net.get_params()
        net.initialize()
        net.get_params()

    def test_with_initialized_module(self, net_cls, module_cls, data):
        X, y = data
        net = net_cls(module_cls(), max_epochs=1)
        net.fit(X, y)

    def test_with_initialized_module_other_params(
            self, net_cls, module_cls, data, capsys):
        X, y = data
        net = net_cls(module_cls(), max_epochs=1, module__num_units=123)
        net.fit(X, y)
        weight = net.module_.dense0.weight.data
        assert weight.shape[0] == 123

        stdout = capsys.readouterr()[0]
        assert "Re-initializing module!" in stdout

    def test_with_initialized_module_non_default(
            self, net_cls, module_cls, data, capsys):
        X, y = data
        net = net_cls(module_cls(num_units=123), max_epochs=1)
        net.fit(X, y)
        weight = net.module_.dense0.weight.data
        assert weight.shape[0] == 123

        stdout = capsys.readouterr()[0]
        assert "Re-initializing module!" not in stdout

    def test_with_initialized_module_partial_fit(
            self, net_cls, module_cls, data, capsys):
        X, y = data
        module = module_cls(num_units=123)
        net = net_cls(module, max_epochs=0)
        net.partial_fit(X, y)

        for p0, p1 in zip(module.parameters(), net.module_.parameters()):
            assert p0.data.shape == p1.data.shape
            assert (p0 == p1).data.all()

        stdout = capsys.readouterr()[0]
        assert "Re-initializing module!" not in stdout

    def test_with_initialized_module_warm_start(
            self, net_cls, module_cls, data, capsys):
        X, y = data
        module = module_cls(num_units=123)
        net = net_cls(module, max_epochs=0, warm_start=True)
        net.partial_fit(X, y)

        for p0, p1 in zip(module.parameters(), net.module_.parameters()):
            assert p0.data.shape == p1.data.shape
            assert (p0 == p1).data.all()

        stdout = capsys.readouterr()[0]
        assert "Re-initializing module!" not in stdout

    def test_with_initialized_sequential(self, net_cls, data, capsys):
        X, y = data
        module = nn.Sequential(
            nn.Linear(X.shape[1], 10),
            nn.ReLU(),
            nn.Linear(10, 2),
            nn.Softmax(dim=-1),
        )
        net = net_cls(module, max_epochs=1)
        net.fit(X, y)

        stdout = capsys.readouterr()[0]
        assert "Re-initializing module!" not in stdout

    def test_call_fit_twice_retrains(self, net_cls, module_cls, data):
        # test that after second fit call, even without entering the
        # fit loop, parameters have changed (because the module was
        # re-initialized)
        X, y = data[0][:100], data[1][:100]
        net = net_cls(module_cls, warm_start=False).fit(X, y)
        params_before = net.module_.parameters()

        net.max_epochs = 0
        net.fit(X, y)
        params_after = net.module_.parameters()

        assert not net.history
        for p0, p1 in zip(params_before, params_after):
            assert (p0 != p1).data.any()

    def test_call_fit_twice_warmstart(self, net_cls, module_cls, data):
        X, y = data[0][:100], data[1][:100]
        net = net_cls(module_cls, warm_start=True).fit(X, y)
        params_before = net.module_.parameters()

        net.max_epochs = 0
        net.fit(X, y)
        params_after = net.module_.parameters()

        assert len(net.history) == 10
        for p0, p1 in zip(params_before, params_after):
            assert (p0 == p1).data.all()

    def test_partial_fit_first_call(self, net_cls, module_cls, data):
        # It should be possible to partial_fit without calling fit first.
        X, y = data[0][:100], data[1][:100]
        # does not raise
        net_cls(module_cls, warm_start=True).partial_fit(X, y)

    def test_call_partial_fit_after_fit(self, net_cls, module_cls, data):
        X, y = data[0][:100], data[1][:100]
        net = net_cls(module_cls, warm_start=False).fit(X, y)
        params_before = net.module_.parameters()

        net.max_epochs = 0
        net.partial_fit(X, y)
        params_after = net.module_.parameters()

        assert len(net.history) == 10
        for p0, p1 in zip(params_before, params_after):
            assert (p0 == p1).data.all()

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
    def test_binary_classification_with_cuda(self, net_cls, module_cls, data):
        X, y = data
        assert y.ndim == 1

        net = net_cls(module_cls, max_epochs=1, device='cuda')
        net.fit(X, y)

    @pytest.mark.parametrize('use_caching', [True, False])
    def test_net_initialized_with_custom_dataset_args(
            self, net_cls, module_cls, data, dataset_cls, use_caching):
        side_effect = []

        class MyDataset(dataset_cls):
            def __init__(self, *args, foo, **kwargs):
                super().__init__(*args, **kwargs)
                side_effect.append(foo)

        net = net_cls(
            module_cls,
            dataset=MyDataset,
            dataset__foo=123,
            max_epochs=1,
            callbacks__train_loss__use_caching=use_caching,
            callbacks__valid_loss__use_caching=use_caching,
            callbacks__valid_acc__use_caching=use_caching,
        )
        net.fit(*data)

        if not use_caching:
            # train/valid split, scoring predict
            assert side_effect == [123, 123]
        else:
            # train/valid split
            assert side_effect == [123]

    @pytest.mark.xfail
    def test_net_initialized_with_initalized_dataset(
            self, net_cls, module_cls, data, dataset_cls):
        net = net_cls(
            module_cls,
            dataset=dataset_cls(*data),
            max_epochs=1,

            # Disable caching to highlight the issue with this
            # test case (mismatching size between y values)
            callbacks__valid_acc__use_caching=False,
        )
        # FIXME: When dataset is initialized, X and y do not matter
        # anymore
        net.fit(*data)  # should not raise

    def test_net_initialized_with_initalized_dataset_and_kwargs_raises(
            self, net_cls, module_cls, data, dataset_cls):
        net = net_cls(
            module_cls,
            dataset=dataset_cls(*data),
            dataset__foo=123,
            max_epochs=1,
        )
        with pytest.raises(TypeError) as exc:
            net.fit(*data)

        expected = ("Trying to pass an initialized Dataset while passing "
                    "Dataset arguments ({'foo': 123}) is not allowed.")
        assert exc.value.args[0] == expected

    def test_repr_uninitialized_works(self, net_cls, module_cls):
        net = net_cls(
            module_cls,
            module__num_units=55,
        )
        result = net.__repr__()
        expected = """<class 'skorch.net.NeuralNetClassifier'>[uninitialized](
  module=<class 'skorch.tests.test_net.MyClassifier'>,
  module__num_units=55,
)"""
        assert result == expected

    def test_repr_initialized_works(self, net_cls, module_cls):
        net = net_cls(
            module_cls,
            module__num_units=42,
        )
        net.initialize()
        result = net.__repr__()
        expected = """<class 'skorch.net.NeuralNetClassifier'>[initialized](
  module_=MyClassifier(
    (dense0): Linear(in_features=20, out_features=42, bias=True)
    (dropout): Dropout(p=0.5)
    (dense1): Linear(in_features=42, out_features=10, bias=True)
    (output): Linear(in_features=10, out_features=2, bias=True)
  ),
)"""
        assert result == expected

    def test_repr_fitted_works(self, net_cls, module_cls, data):
        X, y = data
        net = net_cls(
            module_cls,
            module__num_units=11,
            module__nonlin=nn.PReLU(),
        )
        net.fit(X[:50], y[:50])
        result = net.__repr__()
        expected = """<class 'skorch.net.NeuralNetClassifier'>[initialized](
  module_=MyClassifier(
    (dense0): Linear(in_features=20, out_features=11, bias=True)
    (nonlin): PReLU(num_parameters=1)
    (dropout): Dropout(p=0.5)
    (dense1): Linear(in_features=11, out_features=10, bias=True)
    (output): Linear(in_features=10, out_features=2, bias=True)
  ),
)"""
        assert result == expected

    def test_fit_params_passed_to_module(self, net_cls, data):
        X, y = data
        side_effect = []

        class FPModule(MyClassifier):
            # pylint: disable=arguments-differ
            def forward(self, X, **fit_params):
                side_effect.append(fit_params)
                return super().forward(X)

        net = net_cls(FPModule, max_epochs=1, batch_size=50, train_split=None)
        # remove callbacks to have better control over side_effect
        net.initialize()
        net.callbacks_ = []
        net.fit(X[:100], y[:100], foo=1, bar=2)
        net.fit(X[:100], y[:100], bar=3, baz=4)

        assert len(side_effect) == 4  # 2 epochs à 2 batches
        assert side_effect[0] == dict(foo=1, bar=2)
        assert side_effect[1] == dict(foo=1, bar=2)
        assert side_effect[2] == dict(bar=3, baz=4)
        assert side_effect[3] == dict(bar=3, baz=4)

    def test_fit_params_passed_to_module_in_pipeline(self, net_cls, data):
        X, y = data
        side_effect = []

        class FPModule(MyClassifier):
            # pylint: disable=arguments-differ
            def forward(self, X, **fit_params):
                side_effect.append(fit_params)
                return super().forward(X)

        net = net_cls(FPModule, max_epochs=1, batch_size=50, train_split=None)
        net.initialize()
        net.callbacks_ = []
        pipe = Pipeline([
            ('net', net),
        ])
        pipe.fit(X[:100], y[:100], net__foo=1, net__bar=2)
        pipe.fit(X[:100], y[:100], net__bar=3, net__baz=4)

        assert len(side_effect) == 4  # 2 epochs à 2 batches
        assert side_effect[0] == dict(foo=1, bar=2)
        assert side_effect[1] == dict(foo=1, bar=2)
        assert side_effect[2] == dict(bar=3, baz=4)
        assert side_effect[3] == dict(bar=3, baz=4)

    def test_fit_params_passed_to_train_split(self, net_cls, data):
        X, y = data
        side_effect = []

        # pylint: disable=unused-argument
        def fp_train_split(dataset, y=None, **fit_params):
            side_effect.append(fit_params)
            return dataset, dataset

        class FPModule(MyClassifier):
            # pylint: disable=unused-argument,arguments-differ
            def forward(self, X, **fit_params):
                return super().forward(X)

        net = net_cls(
            FPModule,
            max_epochs=1,
            batch_size=50,
            train_split=fp_train_split,
        )
        net.initialize()
        net.callbacks_ = []
        net.fit(X[:100], y[:100], foo=1, bar=2)
        net.fit(X[:100], y[:100], bar=3, baz=4)

        assert len(side_effect) == 2  # 2 epochs
        assert side_effect[0] == dict(foo=1, bar=2)
        assert side_effect[1] == dict(bar=3, baz=4)

    def test_data_dict_and_fit_params(self, net_cls, data):
        X, y = data

        class FPModule(MyClassifier):
            # pylint: disable=unused-argument,arguments-differ
            def forward(self, X0, X1, **fit_params):
                assert fit_params.get('foo') == 3
                return super().forward(X0)

        net = net_cls(FPModule, max_epochs=1, batch_size=50, train_split=None)
        # does not raise
        net.fit({'X0': X, 'X1': X}, y, foo=3)

    def test_data_dict_and_fit_params_conflicting_names_raises(
            self, net_cls, data):
        X, y = data

        class FPModule(MyClassifier):
            # pylint: disable=unused-argument,arguments-differ
            def forward(self, X0, X1, **fit_params):
                return super().forward(X0)

        net = net_cls(FPModule, max_epochs=1, batch_size=50, train_split=None)

        with pytest.raises(ValueError) as exc:
            net.fit({'X0': X, 'X1': X}, y, X1=3)

        expected = "X and fit_params contain duplicate keys: X1"
        assert exc.value.args[0] == expected

    def test_fit_with_dataset(self, net_cls, module_cls, data, dataset_cls):
        ds = dataset_cls(*data)
        net = net_cls(module_cls, max_epochs=1)
        net.fit(ds, data[1])
        for key in ('train_loss', 'valid_loss', 'valid_acc'):
            assert key in net.history[-1]

    def test_predict_with_dataset(self, net_cls, module_cls, data, dataset_cls):
        ds = dataset_cls(*data)
        net = net_cls(module_cls).initialize()
        y_pred = net.predict(ds)
        y_proba = net.predict_proba(ds)

        assert y_pred.shape[0] == len(ds)
        assert y_proba.shape[0] == len(ds)

    def test_fit_with_dataset_X_y_inaccessible_does_not_raise(
            self, net_cls, module_cls, data):
        class MyDataset(torch.utils.data.Dataset):
            """Dataset with inaccessible X and y"""
            def __init__(self, X, y):
                self.xx = X  # incorrect attribute name
                self.yy = y  # incorrect attribute name

            def __len__(self):
                return len(self.xx)

            def __getitem__(self, i):
                return self.xx[i], self.yy[i]

        ds = MyDataset(*data)
        net = net_cls(module_cls, max_epochs=1)
        net.fit(ds, data[1])  # does not raise

    def test_fit_with_dataset_without_explicit_y(
            self, net_cls, module_cls, dataset_cls, data):
        from skorch.dataset import CVSplit

        net = net_cls(
            module_cls,
            max_epochs=1,
            train_split=CVSplit(stratified=False),
        )
        ds = dataset_cls(*data)
        net.fit(ds, None)  # does not raise
        for key in ('train_loss', 'valid_loss', 'valid_acc'):
            assert key in net.history[-1]

    def test_fit_with_dataset_stratified_without_explicit_y_raises(
            self, net_cls, module_cls, dataset_cls, data):
        from skorch.dataset import CVSplit

        net = net_cls(
            module_cls,
            train_split=CVSplit(stratified=True),
        )
        ds = dataset_cls(*data)
        with pytest.raises(ValueError) as exc:
            net.fit(ds, None)

        msg = "Stratified CV requires explicitely passing a suitable y."
        assert exc.value.args[0] == msg

    # XXX remove once deprecation for use_cuda is phased out
    def test_init_use_cuda_deprecated(self, net_cls, module_cls):
        with pytest.raises(ValueError) as exc:
            net_cls(module_cls, use_cuda=True)

        msg = ("The parameter use_cuda is no longer supported. Use "
               "device='cuda' instead.")
        assert exc.value.args[0] == msg

    @pytest.fixture
    def multiouput_net(self, net_cls, multiouput_module):
        return net_cls(multiouput_module).initialize()

    def test_multioutput_forward_iter(self, multiouput_net, data):
        X = data[0]
        y_infer = next(multiouput_net.forward_iter(X))

        assert isinstance(y_infer, tuple)
        assert len(y_infer) == 3
        assert y_infer[0].shape[0] == min(len(X), multiouput_net.batch_size)

    def test_multioutput_forward(self, multiouput_net, data):
        X = data[0]
        n = len(X)
        y_infer = multiouput_net.forward(X)

        assert isinstance(y_infer, tuple)
        assert len(y_infer) == 3
        for arr in y_infer:
            assert is_torch_data_type(arr)

        # Expecting full output: (number of samples, number of output units)
        assert y_infer[0].shape == (n, 2)
        # Expecting only column 0: (number of samples,)
        assert y_infer[1].shape == (n,)
        # Expecting only every other row: (number of samples/2, number
        # of output units)
        assert y_infer[2].shape == (n // 2, 2)

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="no cuda device")
    def test_multioutput_forward_device_gpu(self, multiouput_net, data):
        X = data[0]
        y_infer = multiouput_net.forward(X, device='cuda:0')

        assert isinstance(y_infer, tuple)
        assert len(y_infer) == 3
        for arr in y_infer:
            assert arr.is_cuda

    def test_multioutput_predict(self, multiouput_net, data):
        X = data[0]
        n = len(X)

        # does not raise
        y_pred = multiouput_net.predict(X)

        # Expecting only 1 column containing predict class:
        # (number of samples,)
        assert y_pred.shape == (n,)
        assert set(y_pred) == {0, 1}

    def test_multiouput_predict_proba(self, multiouput_net, data):
        X = data[0]
        n = len(X)

        # does not raise
        y_proba = multiouput_net.predict_proba(X)

        # Expecting full output: (number of samples, number of output units)
        assert y_proba.shape == (n, 2)
        # Probabilities, hence these limits
        assert y_proba.min() >= 0
        assert y_proba.max() <= 1

    def test_setting_callback_possible(self, net_cls, module_cls):
        from skorch.callbacks import EpochTimer, PrintLog

        net = net_cls(module_cls, callbacks=[('mycb', PrintLog())])
        net.initialize()

        assert isinstance(dict(net.callbacks_)['mycb'], PrintLog)

        net.set_params(callbacks__mycb=EpochTimer())
        assert isinstance(dict(net.callbacks_)['mycb'], EpochTimer)

    def test_setting_callback_default_possible(self, net_cls, module_cls):
        from skorch.callbacks import EpochTimer, PrintLog

        net = net_cls(module_cls)
        net.initialize()

        assert isinstance(dict(net.callbacks_)['print_log'], PrintLog)

        net.set_params(callbacks__print_log=EpochTimer())
        assert isinstance(dict(net.callbacks_)['print_log'], EpochTimer)

    def test_setting_callback_to_none_possible(self, net_cls, module_cls, data):
        from skorch.callbacks import Callback

        X, y = data[0][:30], data[1][:30]  # accelerate test
        side_effects = []

        class DummyCallback(Callback):
            def __init__(self, i):
                self.i = i

            # pylint: disable=unused-argument, arguments-differ
            def on_epoch_end(self, *args, **kwargs):
                side_effects.append(self.i)

        net = net_cls(
            module_cls,
            max_epochs=2,
            callbacks=[
                ('cb0', DummyCallback(0)),
                ('cb1', DummyCallback(1)),
                ('cb2', DummyCallback(2)),
            ],
        )
        net.fit(X, y)

        # all 3 callbacks write to output twice
        assert side_effects == [0, 1, 2, 0, 1, 2]

        # deactivate cb1
        side_effects.clear()
        net.set_params(callbacks__cb1=None)
        net.fit(X, y)

        assert side_effects == [0, 2, 0, 2]

    def test_setting_callback_to_none_and_more_params_during_init_raises(
            self, net_cls, module_cls):
        # if a callback is set to None, setting more params for it
        # should not work
        net = net_cls(
            module_cls, callbacks__print_log=None, callbacks__print_log__sink=1)

        with pytest.raises(ValueError) as exc:
            net.initialize()

        msg = ("Trying to set a parameter for callback print_log "
               "which does not exist.")
        assert exc.value.args[0] == msg

    def test_setting_callback_to_none_and_more_params_later_raises(
            self, net_cls, module_cls):
        # this should work
        net = net_cls(module_cls)
        net.set_params(callbacks__print_log__sink=123)
        net.set_params(callbacks__print_log=None)

        net = net_cls(module_cls)
        net.set_params(callbacks__print_log=None)
        with pytest.raises(ValueError) as exc:
            net.set_params(callbacks__print_log__sink=123)

        msg = ("Trying to set a parameter for callback print_log "
               "which does not exist.")
        assert exc.value.args[0] == msg

    def test_set_params_with_unknown_key_raises(self, net):
        with pytest.raises(ValueError) as exc:
            net.set_params(foo=123)

        # TODO: check error message more precisely, depending on what
        # the intended message shouldb e from sklearn side
        assert exc.value.args[0].startswith('Invalid parameter foo for')

    @pytest.fixture()
    def sequence_module_cls(self):
        """Simple sequence model with variable size dim 1."""
        class Mod(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.l = torch.nn.Linear(1, 1)
            # pylint: disable=arguments-differ
            def forward(self, x):
                n = np.random.randint(1, 4)
                y = self.l(x.float())
                return torch.randn(1, n, 2) + 0 * y
        return Mod

    def test_net_variable_prediction_lengths(
            self, net_cls, sequence_module_cls):
        # neural net should work fine with fixed y_true but varying y_pred
        # sequences.
        X = np.array([1, 5, 3, 6, 2])
        y = np.array([[0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 0], [0, 1, 0]])
        X, y = X[:, np.newaxis], y[:, :, np.newaxis]
        X, y = X.astype('float32'), y.astype('float32')

        net = net_cls(
            sequence_module_cls,
            batch_size=1,
            max_epochs=2,
            train_split=None,
        )

        # Mock loss function
        # pylint: disable=unused-argument
        def loss_fn(y_pred, y_true, **kwargs):
            return y_pred[:, 0, 0]
        net.get_loss = loss_fn

        net.fit(X, y)

    def test_net_variable_label_lengths(self, net_cls, sequence_module_cls):
        # neural net should work fine with varying y_true sequences.
        X = np.array([1, 5, 3, 6, 2])
        y = np.array([[1], [1, 0, 1], [1, 1], [1, 1, 0], [1, 0]])
        X = X[:, np.newaxis].astype('float32')
        y = np.array([np.array(n, dtype='float32')[:, np.newaxis] for n in y])

        net = net_cls(
            sequence_module_cls,
            batch_size=1,
            max_epochs=2,
            train_split=None,
        )

        # Mock loss function
        # pylint: disable=unused-argument
        def loss_fn(y_pred, y_true, **kwargs):
            return y_pred[:, 0, 0]
        net.get_loss = loss_fn

        # Check data complains about y.shape = (n,) but
        # we know that it is actually (n, m) with m in [1;3].
        net.check_data = lambda *_, **kw: None
        net.fit(X, y)

    # classifier-specific test
    def test_takes_log_with_nllloss(self, net_cls, module_cls, data):
        net = net_cls(module_cls, criterion=nn.NLLLoss, max_epochs=1)
        net.initialize()

        mock_loss = Mock(side_effect=nn.NLLLoss())
        net.criterion_.forward = mock_loss
        net.partial_fit(*data)  # call partial_fit to avoid re-initialization

        # check that loss was called with log-probabilities
        for (y_log, _), _ in mock_loss.call_args_list:
            assert (y_log < 0).all()
            y_proba = torch.exp(y_log)
            assert torch.isclose(torch.ones(len(y_proba)), y_proba.sum(1)).all()

    # classifier-specific test
    def test_takes_no_log_without_nllloss(self, net_cls, module_cls, data):
        net = net_cls(module_cls, criterion=nn.BCELoss, max_epochs=1)
        net.initialize()

        mock_loss = Mock(side_effect=nn.NLLLoss())
        net.criterion_.forward = mock_loss
        net.partial_fit(*data)  # call partial_fit to avoid re-initialization

        # check that loss was called with raw probabilities
        for (y_out, _), _ in mock_loss.call_args_list:
            assert not (y_out < 0).all()
            assert torch.isclose(torch.ones(len(y_out)), y_out.sum(1)).all()

    def test_no_grad_during_validation(self, net_cls, module_cls, data):
        """Test that gradient is only calculated during training step,
        not validation step."""

        # pylint: disable=unused-argument
        def check_grad(*args, loss, training, **kwargs):
            if training:
                assert loss.requires_grad
            else:
                assert not loss.requires_grad

        mock_cb = Mock(on_batch_end=check_grad)
        net = net_cls(module_cls, max_epochs=1, callbacks=[mock_cb])
        net.fit(*data)

    @pytest.mark.parametrize('training', [True, False])
    def test_no_grad_during_evaluation_unless_training(
            self, net_cls, module_cls, data, training):
        """Test that gradient is only calculated in training mode
        during evaluation step."""
        from skorch.utils import to_tensor

        net = net_cls(module_cls).initialize()
        Xi = to_tensor(data[0][:3], device='cpu')
        y_eval = net.evaluation_step(Xi, training=training)

        assert y_eval.requires_grad is training

    @pytest.mark.parametrize(
        'net_kwargs,expected_train_batch_size,expected_valid_batch_size',
        [
            ({'batch_size': -1}, 800, 200),
            ({'iterator_train__batch_size': -1}, 800, 128),
            ({'iterator_valid__batch_size': -1}, 128, 200),
        ]
    )
    def test_batch_size_neg_1_uses_whole_dataset(
            self, net_cls, module_cls, data, net_kwargs,
            expected_train_batch_size, expected_valid_batch_size):

        train_loader_mock = Mock(side_effect=torch.utils.data.DataLoader)
        valid_loader_mock = Mock(side_effect=torch.utils.data.DataLoader)

        net = net_cls(module_cls, max_epochs=1,
                      iterator_train=train_loader_mock,
                      iterator_valid=valid_loader_mock,
                      **net_kwargs)
        net.fit(*data)

        train_batch_size = net.history[:, 'batches', 'train_batch_size'][0][0]
        valid_batch_size = net.history[:, 'batches', 'valid_batch_size'][0][0]

        assert train_batch_size == expected_train_batch_size
        assert valid_batch_size == expected_valid_batch_size

        train_kwargs = train_loader_mock.call_args[1]
        valid_kwargs = valid_loader_mock.call_args[1]
        assert train_kwargs['batch_size'] == expected_train_batch_size
        assert valid_kwargs['batch_size'] == expected_valid_batch_size


class MyRegressor(nn.Module):
    """Simple regression module.

    We cannot use the module fixtures from conftest because they are
    not pickleable.

    """
    def __init__(self, num_units=10, nonlin=F.relu):
        super(MyRegressor, self).__init__()

        self.dense0 = nn.Linear(20, num_units)
        self.nonlin = nonlin
        self.dropout = nn.Dropout(0.5)
        self.dense1 = nn.Linear(num_units, 10)
        self.output = nn.Linear(10, 1)

    # pylint: disable=arguments-differ
    def forward(self, X):
        X = self.nonlin(self.dense0(X))
        X = self.dropout(X)
        X = self.nonlin(self.dense1(X))
        X = self.output(X)
        return X


class TestNeuralNetRegressor:
    @pytest.fixture(scope='module')
    def data(self, regression_data):
        return regression_data

    @pytest.fixture(scope='module')
    def module_cls(self):
        return MyRegressor

    @pytest.fixture(scope='module')
    def net_cls(self):
        from skorch.net import NeuralNetRegressor
        return NeuralNetRegressor

    @pytest.fixture(scope='module')
    def net(self, net_cls, module_cls):
        return net_cls(
            module_cls,
            max_epochs=20,
            lr=0.1,
        )

    @pytest.fixture(scope='module')
    def net_fit(self, net, data):
        # Careful, don't call additional fits on this, since that would have
        # side effects on other tests.
        X, y = data
        return net.fit(X, y)

    def test_fit(self, net_fit):
        # fitting does not raise anything
        pass

    def test_net_learns(self, net_fit):
        train_losses = net_fit.history[:, 'train_loss']
        assert train_losses[0] > 2 * train_losses[-1]

    def test_history_default_keys(self, net_fit):
        expected_keys = {'train_loss', 'valid_loss', 'epoch', 'dur', 'batches'}
        for row in net_fit.history:
            assert expected_keys.issubset(row)

    def test_target_1d_raises(self, net, data):
        X, y = data
        with pytest.raises(ValueError) as exc:
            net.fit(X, y.flatten())
        assert exc.value.args[0] == (
            "The target data shouldn't be 1-dimensional but instead have "
            "2 dimensions, with the second dimension having the same size "
            "as the number of regression targets (usually 1). Please "
            "reshape your target data to be 2-dimensional "
            "(e.g. y = y.reshape(-1, 1).")

    def test_predict_predict_proba(self, net_fit, data):
        X = data[0]
        y_pred = net_fit.predict(X)

        # predictions should not be all zeros
        assert not np.allclose(y_pred, 0)

        y_proba = net_fit.predict_proba(X)
        # predict and predict_proba should be identical for regression
        assert np.allclose(y_pred, y_proba, atol=1e-6)
