"""Contains learning rate scheduler callbacks"""

import sys

# pylint: disable=unused-import
import numpy as np
from torch.optim.lr_scheduler import _LRScheduler
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.optim.lr_scheduler import ExponentialLR
from torch.optim.lr_scheduler import LambdaLR
from torch.optim.lr_scheduler import MultiStepLR
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.optim.lr_scheduler import StepLR
from torch.optim.optimizer import Optimizer
from skorch.callbacks import Callback


__all__ = ['LRScheduler', 'WarmRestartLR', 'CyclicLR']


class LRScheduler(Callback):
    """Callback that sets the learning rate of each
    parameter group according to some policy.

    Parameters
    ----------

    policy : str or _LRScheduler class (default='WarmRestartLR')
      Learning rate policy name or scheduler to be used.

    monitor : str or callable (default=None)
      Value of the history to monitor or function/callable. In
      the latter case, the callable receives the net instance as
      argument and is expected to return the score (float) used to
      determine the learning rate adjustment.

    kwargs
      Additional arguments passed to the lr scheduler.

    """

    def __init__(self, policy='WarmRestartLR', monitor='train_loss', **kwargs):
        self.policy = policy
        self.monitor = monitor
        self.kwargs = kwargs

    def initialize(self):
        if isinstance(self.policy, str):
            self.policy_ = getattr(sys.modules[__name__], self.policy)
        else:
            self.policy_ = self.policy
        self.lr_scheduler_ = None
        return self

    def on_train_begin(self, net, **kwargs):
        self.lr_scheduler_ = self._get_scheduler(
            net, self.policy_, **self.kwargs
        )

    def on_epoch_begin(self, net, **kwargs):
        epoch = len(net.history) - 1
        if isinstance(self.lr_scheduler_, ReduceLROnPlateau):
            if callable(self.monitor):
                score = self.monitor(net)
            else:
                score = net.history[-2, self.monitor] if epoch else np.inf
            self.lr_scheduler_.step(score, epoch)
        else:
            self.lr_scheduler_.step(epoch)

    def on_batch_begin(self, net, **kwargs):
        if (
                hasattr(self.lr_scheduler_, 'batch_step') and
                callable(self.lr_scheduler_.batch_step)
        ):
            batch_idx = self._get_batch_idx(net)
            self.lr_scheduler_.batch_step(batch_idx)

    def _get_scheduler(self, net, policy, **scheduler_kwargs):
        """Return scheduler, based on indicated policy, with appropriate
        parameters.
        """
        if policy not in [CyclicLR, ReduceLROnPlateau] and \
           'last_epoch' not in scheduler_kwargs:
            last_epoch = len(net.history) - 1
            scheduler_kwargs['last_epoch'] = last_epoch

        if policy is CyclicLR and \
           'last_batch_idx' not in scheduler_kwargs:
            last_batch_idx = self._get_batch_idx(net)
            scheduler_kwargs['last_batch_idx'] = last_batch_idx
        return policy(net.optimizer_, **scheduler_kwargs)

    def _get_batch_idx(self, net):
        if not net.history:
            return -1
        epoch = len(net.history) - 1
        current_batch_idx = len(net.history[-1, 'batches']) - 1
        batch_cnt = len(net.history[-2, 'batches']) if epoch >= 1 else 0
        return epoch * batch_cnt + current_batch_idx


class WarmRestartLR(_LRScheduler):
    """Stochastic Gradient Descent with Warm Restarts (SGDR) scheduler.

    This scheduler sets the learning rate of each parameter group
    according to stochastic gradient descent with warm restarts (SGDR)
    policy. This policy simulates periodic warm restarts of SGD, where
    in each restart the learning rate is initialize to some value and is
    scheduled to decrease.

    Parameters
    ----------
    optimizer : torch.optimizer.Optimizer instance.
      Optimizer algorithm.

    min_lr : float or list of float (default=1e-6)
      Minimum allowed learning rate during each period for all
      param groups (float) or each group (list).

    max_lr : float or list of float (default=0.05)
      Maximum allowed learning rate during each period for all
      param groups (float) or each group (list).

    base_period : int (default=10)
      Initial restart period to be multiplied at each restart.

    period_mult : int (default=2)
      Multiplicative factor to increase the period between restarts.

    last_epoch : int (default=-1)
      The index of the last valid epoch.

    References
    ----------
    .. [1] Ilya Loshchilov and Frank Hutter, 2017, "Stochastic Gradient
        Descent with Warm Restarts,". "ICLR"
        `<https://arxiv.org/pdf/1608.03983.pdf>`_

    """

    def __init__(
            self, optimizer,
            min_lr=1e-6,
            max_lr=0.05,
            base_period=10,
            period_mult=2,
            last_epoch=-1
        ):
        self.min_lr = self._format_lr('min_lr', optimizer, min_lr)
        self.max_lr = self._format_lr('max_lr', optimizer, max_lr)
        self.base_period = base_period
        self.period_mult = period_mult
        super(WarmRestartLR, self).__init__(optimizer, last_epoch)

    def _format_lr(self, name, optimizer, lr):
        """Return correctly formatted lr for each param group."""
        if isinstance(lr, (list, tuple)):
            if len(lr) != len(optimizer.param_groups):
                raise ValueError("expected {} values for {}, got {}".format(
                    len(optimizer.param_groups), name, len(lr)))
            return np.array(lr)
        else:
            return lr * np.ones(len(optimizer.param_groups))

    def _get_current_lr(self, min_lr, max_lr, period, epoch):
        return min_lr + 0.5*(max_lr-min_lr)*(1+ np.cos(epoch * np.pi/period))

    def get_lr(self):
        epoch_idx = float(self.last_epoch)
        current_period = float(self.base_period)
        while epoch_idx / current_period > 1.0:
            epoch_idx -= current_period + 1
            current_period *= self.period_mult

        current_lrs = self._get_current_lr(
            self.min_lr,
            self.max_lr,
            current_period,
            epoch_idx
        )
        return current_lrs.tolist()


class CyclicLR(object):
    """Sets the learning rate of each parameter group according to
    cyclical learning rate policy (CLR). The policy cycles the learning
    rate between two boundaries with a constant frequency, as detailed in
    the paper.
    The distance between the two boundaries can be scaled on a per-iteration
    or per-cycle basis.

    Cyclical learning rate policy changes the learning rate after every batch.
    ``batch_step`` should be called after a batch has been used for training.
    To resume training, save `last_batch_idx` and use it to instantiate
    ``CycleLR``.

    This class has three built-in policies, as put forth in the paper:

    "triangular":
        A basic triangular cycle w/ no amplitude scaling.
    "triangular2":
        A basic triangular cycle that scales initial amplitude by half each
        cycle.
    "exp_range":
        A cycle that scales initial amplitude by gamma**(cycle iterations)
        at each cycle iteration.

    This implementation was adapted from the github repo:
    `bckenstler/CLR <https://github.com/bckenstler/CLR>`_

    Parameters
    ----------
    optimizer : torch.optimizer.Optimizer instance.
      Optimizer algorithm.

    base_lr : float or list of float (default=1e-3)
      Initial learning rate which is the lower boundary in the
      cycle for each param groups (float) or each group (list).

    max_lr : float or list of float (default=6e-3)
      Upper boundaries in the cycle for each parameter group (float)
      or each group (list). Functionally, it defines the cycle
      amplitude (max_lr - base_lr). The lr at any cycle is the sum
      of base_lr and some scaling of the amplitude; therefore max_lr
      may not actually be reached depending on scaling function.

    step_size : int (default=2000)
      Number of training iterations per half cycle. Authors suggest
      setting step_size 2-8 x training iterations in epoch.

    mode : str (default='triangular')
      One of {triangular, triangular2, exp_range}. Values correspond
      to policies detailed above. If scale_fn is not None, this
      argument is ignored.

    gamma : float (default=1.0)
      Constant in 'exp_range' scaling function:
      gamma**(cycle iterations)

    scale_fn : function (default=None)
      Custom scaling policy defined by a single argument lambda
      function, where 0 <= scale_fn(x) <= 1 for all x >= 0.
      mode paramater is ignored.

    scale_mode : str (default='cycle')
      One of {'cycle', 'iterations'}. Defines whether scale_fn
      is evaluated on cycle number or cycle iterations (training
      iterations since start of cycle).

    last_batch_idx : int (default=-1)
      The index of the last batch.

    Examples
    --------

    >>> optimizer = torch.optim.SGD(model.parameters(), lr=0.1, momentum=0.9)
    >>> scheduler = torch.optim.CyclicLR(optimizer)
    >>> data_loader = torch.utils.data.DataLoader(...)
    >>> for epoch in range(10):
    >>>     for batch in data_loader:
    >>>         scheduler.batch_step()
    >>>         train_batch(...)

    References
    ----------

    .. [1] Leslie N. Smith, 2017, "Cyclical Learning Rates for
        Training Neural Networks,". "ICLR"
        `<https://arxiv.org/abs/1506.01186>`_

    """

    def __init__(self, optimizer, base_lr=1e-3, max_lr=6e-3,
                 step_size=2000, mode='triangular', gamma=1.,
                 scale_fn=None, scale_mode='cycle',
                 last_batch_idx=-1):

        if not isinstance(optimizer, Optimizer):
            raise TypeError('{} is not an Optimizer'.format(
                type(optimizer).__name__))
        self.optimizer = optimizer
        self.base_lrs = self._format_lr('base_lr', optimizer, base_lr)
        self.max_lrs = self._format_lr('max_lr', optimizer, max_lr)
        self.step_size = step_size

        if mode not in ['triangular', 'triangular2', 'exp_range'] \
                and scale_fn is None:
            raise ValueError('mode is invalid and scale_fn is None')

        self.mode = mode
        self.gamma = gamma

        if scale_fn is None:
            if self.mode == 'triangular':
                self.scale_fn = self._triangular_scale_fn
                self.scale_mode = 'cycle'
            elif self.mode == 'triangular2':
                self.scale_fn = self._triangular2_scale_fn
                self.scale_mode = 'cycle'
            elif self.mode == 'exp_range':
                self.scale_fn = self._exp_range_scale_fn
                self.scale_mode = 'iterations'
        else:
            self.scale_fn = scale_fn
            self.scale_mode = scale_mode

        self.batch_step(last_batch_idx + 1)
        self.last_batch_idx = last_batch_idx

    def _format_lr(self, name, optimizer, lr):
        """Return correctly formatted lr for each param group."""
        if isinstance(lr, (list, tuple)):
            if len(lr) != len(optimizer.param_groups):
                raise ValueError("expected {} values for {}, got {}".format(
                    len(optimizer.param_groups), name, len(lr)))
            return np.array(lr)
        else:
            return lr * np.ones(len(optimizer.param_groups))

    def step(self, epoch=None):
        """Not used by ``CyclicLR``, use batch_step instead."""
        pass

    def batch_step(self, batch_idx=None):
        """Updates the learning rate for the batch index: ``batch_idx``.
        If ``batch_idx`` is None, ``CyclicLR`` will use an internal
        batch index to keep track of the index.
        """
        if batch_idx is None:
            batch_idx = self.last_batch_idx + 1
        self.last_batch_idx = batch_idx
        for param_group, lr in zip(self.optimizer.param_groups, self.get_lr()):
            param_group['lr'] = lr

    # pylint: disable=unused-argument
    def _triangular_scale_fn(self, x):
        """Cycle amplitude remains contant"""
        return 1.

    def _triangular2_scale_fn(self, x):
        """
        Decreases the cycle amplitude by half after each period,
        while keeping the base lr constant.
        """
        return 1 / (2. ** (x - 1))

    def _exp_range_scale_fn(self, x):
        """
        Scales the cycle amplitude by a factor ``gamma**x``,
        while keeping the base lr constant.
        """
        return self.gamma**(x)

    def get_lr(self):
        """Calculates the learning rate at batch index:
        ``self.last_batch_idx``.
        """
        step_size = float(self.step_size)
        cycle = np.floor(1 + self.last_batch_idx / (2 * step_size))
        x = np.abs(self.last_batch_idx / step_size - 2 * cycle + 1)

        lrs = []
        for base_lr, max_lr in zip(self.base_lrs, self.max_lrs):
            base_height = (max_lr - base_lr) * np.maximum(0, (1 - x))
            if self.scale_mode == 'cycle':
                lr = base_lr + base_height * self.scale_fn(cycle)
            else:
                lr = base_lr + base_height * self.scale_fn(self.last_batch_idx)
            lrs.append(lr)
        return lrs
