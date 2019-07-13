"""Module implementing StandardRunner."""

from __future__ import print_function
import torch
import importlib
import abc
from deepobs import config as global_config
from .. import config
from .. import testproblems
from . import runner_utils
from deepobs.abstract_runner.abstract_runner import Runner
import numpy as np


class PTRunner(Runner):
    """The abstract class for runner in the pytorch framework."""

    def __init__(self, optimizer_class, hyperparameter_names):
        super(PTRunner, self).__init__(optimizer_class, hyperparameter_names)

    @abc.abstractmethod
    def training(self, tproblem, hyperparams, num_epochs, print_train_iter, train_log_interval, tb_log, tb_log_dir, **training_params):
        return

    def run(self,
            testproblem = None,
            hyperparams = None,
            batch_size = None,
            num_epochs = None,
            random_seed=None,
            data_dir=None,
            output_dir=None,
            weight_decay=None,
            no_logs=None,
            train_log_interval = None,
            print_train_iter = None,
            tb_log = None,
            tb_log_dir = None,
            **training_params
            ):

        args = self.parse_args(testproblem,
            hyperparams,
            batch_size,
            num_epochs,
            random_seed,
            data_dir,
            output_dir,
            weight_decay,
            no_logs,
            train_log_interval,
            print_train_iter,
            tb_log,
            tb_log_dir,
            **training_params)

        # overwrite locals after argparse
        testproblem = args['testproblem']
        hyperparams = args['hyperparams']
        batch_size = args['batch_size']
        num_epochs = args['num_epochs']
        random_seed = args['random_seed']
        data_dir = args['data_dir']
        output_dir = args['output_dir']
        weight_decay = args['weight_decay']
        no_logs = args['weight_decay']
        training_params = args['training_params']
        tb_log_dir = args['tb_log_dir']
        tb_log  = args['tb_log']
        train_log_interval = args['train_log_interval']
        print_train_iter = args['print_train_iter']

        if batch_size is None:
            batch_size = global_config.get_testproblem_default_setting(testproblem)['batch_size']
        if num_epochs is None:
            num_epochs = global_config.get_testproblem_default_setting(testproblem)['num_epochs']

        if data_dir is not None:
            config.set_data_dir(data_dir)

        tproblem = self.create_testproblem(testproblem, batch_size, weight_decay, random_seed)

        output = self.training(tproblem, hyperparams, num_epochs, print_train_iter, train_log_interval, tb_log, tb_log_dir, **training_params)
        output = self._post_process_output(output, 
                                           testproblem, 
                                           batch_size, 
                                           num_epochs, 
                                           random_seed, 
                                           weight_decay, 
                                           hyperparams)

        if not no_logs:
            run_folder_name, file_name = self.create_output_directory(output_dir, output)
            self.write_output(output, run_folder_name, file_name)

        return output

    @staticmethod
    def create_testproblem(testproblem, batch_size, weight_decay, random_seed):
        """Sets up the deepobs.pytorch.testproblems.testproblem instance.
        Args:
            testproblem (str): The name of the testproblem.
            batch_size (int): Batch size that is used for training
            weight_decay (float): Regularization factor
            random_seed (int): The random seed of the framework
        Returns:
            tproblem: An instance of deepobs.pytorch.testproblems.testproblem
        """
        # set the seed and GPU determinism
        if config.get_is_deterministic():
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        else:
            torch.backends.cudnn.deterministic = False
            torch.backends.cudnn.benchmark = True
        np.random.seed(random_seed)
        torch.manual_seed(random_seed)

        # Find testproblem by name and instantiate with batch size and weight decay.
        try:
            testproblem_mod = importlib.import_module(testproblem)
            testproblem_cls = getattr(testproblem_mod, testproblem)
            print("Loading local testproblem.")
        except:
            testproblem_cls = getattr(testproblems, testproblem)

        # if the user specified a weight decay, use that one
        if weight_decay is not None:
            tproblem = testproblem_cls(batch_size, weight_decay)
        # else use the default of the testproblem
        else:
            tproblem = testproblem_cls(batch_size)

        # Set up the testproblem.
        tproblem.set_up()
        return tproblem

    # Wrapper functions for the evaluation phase.
    @staticmethod
    def evaluate(tproblem, test=True):
        """Evaluates the performance of the current state of the model
        of the testproblem instance.
        Has to be called in the beggining of every epoch within the
        training method. Returns the losses and accuracies.
        Args:
            tproblem (testproblem): The testproblem instance to evaluate
            test (bool): Whether tproblem is evaluated on the test set.
            If false, it is evaluated on the train evaluation set.
        Returns:
            loss (float): The loss of the current state.
            accuracy (float): The accuracy of the current state.
        """

        if test:
            tproblem.test_init_op()
            msg = "TEST:"
        else:
            tproblem.train_eval_init_op()
            msg = "TRAIN:"

        # evaluation loop over every batch of the corresponding evaluation set
        loss = 0.0
        accuracy = 0.0
        batchCount = 0.0
        while True:
            try:
                batch_loss, batch_accuracy = tproblem.get_batch_loss_and_accuracy()
                batchCount += 1.0
                loss += batch_loss.item()
                accuracy += batch_accuracy
            except StopIteration:
                break

        loss /= batchCount
        accuracy /= batchCount

        # if the testproblem has a regularization, add the regularization loss of the current network parameters.
        if hasattr(tproblem, 'get_regularization_loss'):
            loss += tproblem.get_regularization_loss().item()

        if accuracy != 0.0:
            print("{0:s} loss {1:g}, acc {2:f}".format(msg, loss, accuracy))
        else:
            print("{0:s} loss {1:g}".format(msg, loss))

        return loss, accuracy


class StandardRunner(PTRunner):
    """A standard runner. Can run a normal training loop with fixed
    hyperparams. It should be used as a template to implement custom runners.

    Methods:
        training: Performs the training on a testproblem instance.
    """

    def __init__(self, optimizer_class, hyperparameter_names):
        super(StandardRunner, self).__init__(optimizer_class, hyperparameter_names)

    def training(self, tproblem, hyperparams, num_epochs, print_train_iter, train_log_interval, tb_log, tb_log_dir):

        opt = self._optimizer_class(tproblem.net.parameters(), **hyperparams)

        # Lists to log train/test loss and accuracy.
        train_losses = []
        test_losses = []
        train_accuracies = []
        test_accuracies = []

        minibatch_train_losses = []

        if tb_log:
            try:
                from torch.utils.tensorboard import SummaryWriter
                summary_writer = SummaryWriter(log_dir=tb_log_dir)
            except ImportError as e:
                print('Not possible to use tensorboard for pytorch. Reason:', e)
                tb_log = False
            global_step = 0

        for epoch_count in range(num_epochs+1):
            # Evaluate at beginning of epoch.
            print("********************************")
            print("Evaluating after {0:d} of {1:d} epochs...".format(epoch_count, num_epochs))

            loss_, acc_ = self.evaluate(tproblem, test=False)
            train_losses.append(loss_)
            train_accuracies.append(acc_)

            loss_, acc_ = self.evaluate(tproblem, test=True)
            test_losses.append(loss_)
            test_accuracies.append(acc_)

            print("********************************")

            # Break from train loop after the last round of evaluation
            if epoch_count == num_epochs:
                break

            ### Training ###

            # set to training mode
            tproblem.train_init_op()
            batch_count = 0
            while True:
                try:
                    opt.zero_grad()
                    batch_loss, _ = tproblem.get_batch_loss_and_accuracy()
                    # if the testproblem has a regularization, add the regularization loss.
                    if hasattr(tproblem, 'get_regularization_loss'):
                        regularizer_loss = tproblem.get_regularization_loss()
                        batch_loss += regularizer_loss

                    batch_loss.backward()
                    opt.step()

                    if batch_count % train_log_interval == 0:
                        minibatch_train_losses.append(batch_loss.item())
                        if print_train_iter:
                            print("Epoch {0:d}, step {1:d}: loss {2:g}".format(epoch_count, batch_count, batch_loss))
                        if tb_log:
                            summary_writer.add_scalar('loss', batch_loss.item(), global_step)

                    batch_count += 1
                    global_step += 1

                except StopIteration:
                    break

            if np.isnan(batch_loss.item()) or np.isinf(batch_loss.item()):
                train_losses, test_losses, train_accuracies, test_accuracies, minibatch_train_losses = self._abort_routine(epoch_count,
                                                                                                   num_epochs,
                                                                                                   train_losses,
                                                                                                   test_losses,
                                                                                                   train_accuracies,
                                                                                                   test_accuracies,
                                                                                                minibatch_train_losses)
                break
            else:
                continue

        # Put results into output dictionary.
        output = {
            "train_losses": train_losses,
            "test_losses": test_losses,
            "minibatch_train_losses": minibatch_train_losses,
            "train_accuracies": train_accuracies,
            "test_accuracies": test_accuracies
        }

        return output


class LearningRateScheduleRunner(PTRunner):
    """A runner for learning rate schedules. Can run a normal training loop with fixed hyperparams or a learning rate
    schedule. It should be used as a template to implement custom runners.

    Methods:
        training: Performs the training on a testproblem instance.
    """

    def __init__(self, optimizer_class, hyperparameter_names):

        super(LearningRateScheduleRunner, self).__init__(optimizer_class, hyperparameter_names)

    def training(self,
            tproblem,
            hyperparams,
            num_epochs,
            # the following are the training_params
            lr_sched_epochs=None,
            lr_sched_factors=None,
            train_log_interval=10,
            print_train_iter=False):

        """Args:
                tproblem (testproblem): The testproblem instance to train on.
                hyperparams (dict): The optimizer hyperparameters to use for the training.
                num_epochs (int): The number of training epochs.

            **training_params are:
                lr_sched_epochs (list): The epochs where to adjust the learning rate.
                lr_sched_factors (list): The corresponding factors by which to adjust the learning rate.
                train_log_interval (int): When to log the minibatch loss/accuracy.
                print_train_iter (bool): Whether to print the training progress at every train_log_interval

            Returns:
                output (dict): The logged metrices. Is of the form:
                    {'test_losses' : test_losses
                     'train_losses': train_losses,
                     'test_accuracies': test_accuracies,
                     'train_accuracies': train_accuracies,
                     'analyzable_training_params': {...}
                     }

            where the metrices values are lists that were filled during training
            and the key 'analyzable_training_params' holds a dict of training
            parameters that should be taken into account in the analysis later on.
            These can be, for example, learning rate schedules. Or in the easiest
            case, this dict is empty.
        """

        opt = self._optimizer_class(tproblem.net.parameters(), **hyperparams)
        if lr_sched_epochs is not None:
            lr_schedule = runner_utils.make_lr_schedule(optimizer=opt, lr_sched_epochs=lr_sched_epochs, lr_sched_factors=lr_sched_factors)

        # Lists to log train/test loss and accuracy.
        train_losses = []
        test_losses = []
        train_accuracies = []
        test_accuracies = []

        minibatch_train_losses = []

        for epoch_count in range(num_epochs+1):
            # Evaluate at beginning of epoch.
            print("********************************")
            print("Evaluating after {0:d} of {1:d} epochs...".format(epoch_count, num_epochs))

            loss_, acc_ = self.evaluate(tproblem, test=False)
            train_losses.append(loss_)
            train_accuracies.append(acc_)

            loss_, acc_ = self.evaluate(tproblem, test=True)
            test_losses.append(loss_)
            test_accuracies.append(acc_)

            print("********************************")

            # Break from train loop after the last round of evaluation
            if epoch_count == num_epochs:
                break

            ### Training ###

            if lr_sched_epochs is not None:
                # get the next learning rate
                lr_schedule.step()
                if epoch_count in lr_sched_epochs:
                    print("Setting learning rate to {0}".format(lr_schedule.get_lr()))

            # set to training mode
            tproblem.train_init_op()
            batch_count = 0
            while True:
                try:
                    opt.zero_grad()
                    batch_loss, _ = tproblem.get_batch_loss_and_accuracy()
                    # if the testproblem has a regularization, add the regularization loss.
                    if hasattr(tproblem, 'get_regularization_loss'):
                        regularizer_loss = tproblem.get_regularization_loss()
                        batch_loss += regularizer_loss

                    batch_loss.backward()
                    opt.step()

                    if batch_count % train_log_interval == 0:
                        minibatch_train_losses.append(batch_loss.item())
                        if print_train_iter:
                            print("Epoch {0:d}, step {1:d}: loss {2:g}".format(epoch_count, batch_count, batch_loss))
                    batch_count += 1

                except StopIteration:
                    break

            # break from training if it goes wrong
            if np.isnan(batch_loss.item()) or np.isinf(batch_loss.item()):
                train_losses, test_losses, train_accuracies, test_accuracies = self._abort_routine(epoch_count,
                                                                                                   num_epochs,
                                                                                                   train_losses,
                                                                                                   test_losses,
                                                                                                   train_accuracies,
                                                                                                   test_accuracies)
                break
            else:
                continue

        # Put results into output dictionary.
        output = {
            "train_losses": train_losses,
            "test_losses": test_losses,
            # dont need minibatch train losses at the moment
#            "minibatch_train_losses": minibatch_train_losses,
            "train_accuracies": train_accuracies,
            "test_accuracies": test_accuracies,
            "analyzable_training_params": {
                    "lr_sched_epochs": lr_sched_epochs,
                    "lr_sched_factors": lr_sched_factors
                    }
        }

        return output