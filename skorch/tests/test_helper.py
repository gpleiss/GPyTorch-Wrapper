"""Test for helper.py"""

import numpy as np
import pytest
from sklearn.datasets import make_classification


class TestSliceDict:
    def assert_dicts_equal(self, d0, d1):
        assert d0.keys() == d1.keys()
        for key in d0.keys():
            assert np.allclose(d0[key], d1[key])

    @pytest.fixture
    def data(self):
        X, y = make_classification(100, 20, n_informative=10, random_state=0)
        return X.astype(np.float32), y

    @pytest.fixture(scope='session')
    def sldict_cls(self):
        from skorch.helper import SliceDict
        return SliceDict

    @pytest.fixture
    def sldict(self, sldict_cls):
        return sldict_cls(
            f0=np.arange(4),
            f1=np.arange(12).reshape(4, 3),
        )

    def test_init_inconsistent_shapes(self, sldict_cls):
        with pytest.raises(ValueError) as exc:
            sldict_cls(f0=np.ones((10, 5)), f1=np.ones((11, 5)))
        assert str(exc.value) == (
            "Initialized with items of different lengths: 10, 11")

    @pytest.mark.parametrize('item', [
        np.ones(4),
        np.ones((4, 1)),
        np.ones((4, 4)),
        np.ones((4, 10, 7)),
        np.ones((4, 1, 28, 28)),
    ])
    def test_set_item_correct_shape(self, sldict, item):
        # does not raise
        sldict['f2'] = item

    @pytest.mark.parametrize('item', [
        np.ones(3),
        np.ones((1, 100)),
        np.ones((5, 1000)),
        np.ones((1, 100, 10)),
        np.ones((28, 28, 1, 100)),
    ])
    def test_set_item_incorrect_shape_raises(self, sldict, item):
        with pytest.raises(ValueError) as exc:
            sldict['f2'] = item
        assert str(exc.value) == (
            "Cannot set array with shape[0] != 4")

    @pytest.mark.parametrize('key', [1, 1.2, (1, 2), [3]])
    def test_set_item_incorrect_key_type(self, sldict, key):
        with pytest.raises(TypeError) as exc:
            sldict[key] = np.ones((100, 5))
        assert str(exc.value).startswith("Key must be str, not <")

    @pytest.mark.parametrize('item', [
        np.ones(3),
        np.ones((1, 100)),
        np.ones((5, 1000)),
        np.ones((1, 100, 10)),
        np.ones((28, 28, 1, 100)),
    ])
    def test_update_incorrect_shape_raises(self, sldict, item):
        with pytest.raises(ValueError) as exc:
            sldict.update({'f2': item})
        assert str(exc.value) == (
            "Cannot set array with shape[0] != 4")

    @pytest.mark.parametrize('item', [123, 'hi', [1, 2, 3]])
    def test_set_first_item_no_shape_raises(self, sldict_cls, item):
        with pytest.raises(AttributeError):
            sldict_cls(f0=item)

    @pytest.mark.parametrize('kwargs, expected', [
        ({}, 0),
        (dict(a=np.zeros(12)), 12),
        (dict(a=np.zeros(12), b=np.ones((12, 5))), 12),
        (dict(a=np.ones((10, 1, 1)), b=np.ones((10, 10)), c=np.ones(10)), 10),
    ])
    def test_len_and_shape(self, sldict_cls, kwargs, expected):
        sldict = sldict_cls(**kwargs)
        assert len(sldict) == expected
        assert sldict.shape == (expected,)

    def test_get_item_str_key(self, sldict_cls):
        sldict = sldict_cls(a=np.ones(5), b=np.zeros(5))
        assert (sldict['a'] == np.ones(5)).all()
        assert (sldict['b'] == np.zeros(5)).all()

    @pytest.mark.parametrize('sl, expected', [
        (slice(0, 1), {'f0': np.array([0]), 'f1': np.array([[0, 1, 2]])}),
        (slice(1, 2), {'f0': np.array([1]), 'f1': np.array([[3, 4, 5]])}),
        (slice(0, 2), {'f0': np.array([0, 1]),
                       'f1': np.array([[0, 1, 2], [3, 4, 5]])}),
        (slice(0, None), dict(f0=np.arange(4),
                              f1=np.arange(12).reshape(4, 3))),
        (slice(-1, None), {'f0': np.array([3]),
                           'f1': np.array([[9, 10, 11]])}),
        (slice(None, None, -1), dict(f0=np.arange(4)[::-1],
                                     f1=np.arange(12).reshape(4, 3)[::-1])),
    ])
    def test_get_item_slice(self, sldict_cls, sldict, sl, expected):
        sliced = sldict[sl]
        self.assert_dicts_equal(sliced, sldict_cls(**expected))

    def test_slice_list(self, sldict, sldict_cls):
        result = sldict[[0, 2]]
        expected = sldict_cls(
            f0=np.array([0, 2]),
            f1=np.array([[0, 1, 2], [6, 7, 8]]))
        self.assert_dicts_equal(result, expected)

    def test_slice_mask(self, sldict, sldict_cls):
        result = sldict[np.array([1, 0, 1, 0]).astype(bool)]
        expected = sldict_cls(
            f0=np.array([0, 2]),
            f1=np.array([[0, 1, 2], [6, 7, 8]]))
        self.assert_dicts_equal(result, expected)

    def test_slice_int(self, sldict):
        with pytest.raises(ValueError) as exc:
            # pylint: disable=pointless-statement
            sldict[0]
        assert str(exc.value) == 'SliceDict cannot be indexed by integers.'

    def test_len_sliced(self, sldict):
        assert len(sldict) == 4
        for i in range(1, 4):
            assert len(sldict[:i]) == i

    def test_str_repr(self, sldict, sldict_cls):
        loc = locals().copy()
        loc.update({'array': np.array, 'SliceDict': sldict_cls})
        # pylint: disable=eval-used
        result = eval(str(sldict), globals(), loc)
        self.assert_dicts_equal(result, sldict)

    def test_iter_over_keys(self, sldict):
        found_keys = {key for key in sldict}
        expected_keys = {'f0', 'f1'}
        assert found_keys == expected_keys

    def test_grid_search_with_dict_works(
            self, sldict_cls, data, classifier_module):
        from sklearn.model_selection import GridSearchCV
        from skorch import NeuralNetClassifier

        net = NeuralNetClassifier(classifier_module)
        X, y = data
        X = sldict_cls(X=X)
        params = {
            'lr': [0.01, 0.02],
            'max_epochs': [10, 20],
        }
        gs = GridSearchCV(net, params, refit=True, cv=3, scoring='accuracy')
        gs.fit(X, y)
        print(gs.best_score_, gs.best_params_)
