#  Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  
#  Licensed under the Apache License, Version 2.0 (the 'License').
#  You may not use this file except in compliance with the License.
#  A copy of the License is located at
#  
#      http://www.apache.org/licenses/LICENSE-2.0
#  
#  or in the 'license' file accompanying this file. This file is distributed 
#  on an 'AS IS' BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either 
#  express or implied. See the License for the specific language governing 
#  permissions and limitations under the License.

import pytest
from mock import patch, call, MagicMock, ANY
from test.unit.utils import mock_import_modules


# Mock out tensorflow modules.
@pytest.fixture(scope='module')
def modules():
    modules_to_mock = [
        'numpy',
        'grpc.beta',
        'tensorflow.python.framework',
        'tensorflow.core.framework',
        'tensorflow.core.protobuf',
        'tensorflow_serving.apis',
        'tensorflow.python.saved_model.signature_constants',
        'google.protobuf.json_format',
        'tensorflow.core.example',
        'grpc.framework.interfaces.face.face'
    ]
    mock, modules = mock_import_modules(modules_to_mock)

    patcher = patch.dict('sys.modules', modules)
    patcher.start()
    yield mock
    patcher.stop()


MOCK_SCRIPT = {}
HOSTS = ['algo-1', 'algo-2', 'algo-3']
CURRENT_HOST = ['algo-1']
MODEL_PATH = 'a/mock/path'
TRAIN_DIR = 'another/mock/path'
INPUT_CHANNELS = {'training': TRAIN_DIR}
HYPERPARAMETERS = {'strparam': 'strval', 'intparam': 789}

class EmptyModule(object):
    pass


@pytest.fixture(scope='module')
def trainer_module(modules):
    import tf_container.trainer
    yield tf_container.trainer


@pytest.fixture
def trainer(trainer_module):
    yield trainer_module.Trainer(customer_script=MOCK_SCRIPT,
                                 current_host=CURRENT_HOST,
                                 hosts=HOSTS,
                                 model_path=MODEL_PATH,
                                 input_channels=INPUT_CHANNELS.copy(),
                                 customer_params=HYPERPARAMETERS.copy())


def test_special_params_defaulting(trainer_module):
    trainer = trainer_module.Trainer(customer_script=MOCK_SCRIPT,
                                     current_host=CURRENT_HOST,
                                     hosts=HOSTS,
                                     model_path=MODEL_PATH)
    assert trainer.customer_params['save_checkpoints_secs'] == 300


def test_build_run_config(modules, trainer):
    trainer.customer_params['save_checkpoints_secs'] = 123

    conf = trainer._build_run_config()

    modules.estimator.RunConfig.assert_called_with(model_dir=MODEL_PATH, save_checkpoints_secs=123)
    assert modules.estimator.RunConfig.return_value == conf


def test_user_estimator_fn(trainer):
    fake_run_config = 'fakerunconfig'
    fake_estimator = 'fakeestimator'
    expected_hps = trainer.customer_params.copy()
    # Set up "customer script".
    def customer_estimator_fn(run_config, hyperparameters):
        assert run_config == fake_run_config
        assert hyperparameters == expected_hps
        return fake_estimator

    customer_script = EmptyModule()
    customer_script.estimator_fn = customer_estimator_fn
    trainer.customer_script = customer_script

    estimator = trainer._build_estimator(fake_run_config)

    assert estimator == fake_estimator


def test_user_keras_model_fn(modules, trainer):
    fake_run_config = 'fakerunconfig'
    fake_keras_model = 'fakekerasmodel'
    expected_hps = trainer.customer_params.copy()
    # Set up "customer script".
    def customer_keras_model_fn(hyperparameters):
        assert hyperparameters == expected_hps
        return fake_keras_model

    customer_script = EmptyModule()
    customer_script.keras_model_fn = customer_keras_model_fn
    trainer.customer_script = customer_script

    estimator = trainer._build_estimator(fake_run_config)

    model_to_estimator = modules.keras.estimator.model_to_estimator
    model_to_estimator.assert_called_with(keras_model=fake_keras_model, config=fake_run_config)
    assert estimator == model_to_estimator.return_value


def test_user_model_fn(modules, trainer):
    fake_run_config = 'fakerunconfig'
    fake_model_fn = 'fakemodelfn'
    expected_hps = trainer.customer_params.copy()
    customer_script = EmptyModule()
    customer_script.model_fn = fake_model_fn
    trainer.customer_script = customer_script

    estimator = trainer._build_estimator(fake_run_config)

    estimator_mock = modules.estimator.Estimator
    estimator_mock.assert_called_with(model_fn=fake_model_fn, params=expected_hps, config=fake_run_config)
    assert estimator == estimator_mock.return_value


def test_build_train_spec(modules, trainer):
    tensor_dict = {'inputs': ['faketensor']}
    labels = ['fakelabels']
    # We add some defaulted hyperparameters into the customer params.
    expected_hps = HYPERPARAMETERS.copy()
    expected_hps['save_checkpoints_secs'] = 300

    # Set up "customer script".
    def customer_train_input_fn(training_dir, hyperparameters):
        assert training_dir == TRAIN_DIR
        assert hyperparameters == expected_hps
        return tensor_dict, labels
    customer_script = EmptyModule()
    customer_script.train_input_fn = customer_train_input_fn

    trainer.train_steps = 987
    trainer.customer_script = customer_script

    spec = trainer._build_train_spec()

    modules.estimator.TrainSpec.assert_called_with(ANY, max_steps=987)
    assert modules.estimator.TrainSpec.return_value == spec
    # Assert that we passed a 0-arg function to TrainSpec as the train_input_fn, that when called,
    # Invokes the customer's train_input_fn with the correct training_dir and hyperparameters.
    train_input_fn = modules.estimator.TrainSpec.call_args[0][0]
    returned_dict, returned_labels = train_input_fn()
    assert (tensor_dict, labels) == (returned_dict, returned_labels)


def test_build_eval_spec_with_serving(modules, trainer):
    # Special hyperparameters passed in by customer should be passed to EvalSpec
    eval_params = {'throttle_secs': 13,
                   'start_delay_secs': 56}
    trainer.customer_params.update(eval_params)
    expected_hps = trainer.customer_params.copy()

    # Set up "customer script".
    tensor_dict = {'inputs': ['faketensor']}
    labels = ['fakelabels']
    def customer_eval_input_fn(training_dir, params):
        assert training_dir == TRAIN_DIR
        assert params == expected_hps
        return tensor_dict, labels
    input_receiver = 'fakeservinginputreceiver'
    def customer_serving_input_fn(params):
        assert params == expected_hps
        return input_receiver
    customer_script = EmptyModule()
    customer_script.eval_input_fn = customer_eval_input_fn
    customer_script.serving_input_fn = customer_serving_input_fn
    trainer.customer_script = customer_script
    # Set a non-default eval_steps, which should be propaated through to the EvalSpec
    trainer.eval_steps = 567

    spec = trainer._build_eval_spec()

    exporter_mock = modules.estimator.LatestExporter
    exporter_mock.assert_called_with('Servo', serving_input_receiver_fn=ANY)
    _, kwargs = exporter_mock.call_args
    serving_input_fn = kwargs['serving_input_receiver_fn']
    returned_input_receiver = serving_input_fn()
    assert input_receiver == returned_input_receiver

    evalspec_mock = modules.estimator.EvalSpec
    evalspec_mock.assert_called_with(ANY, steps=567, exporters=ANY, throttle_secs=13, start_delay_secs=56)
    args, kwargs = evalspec_mock.call_args
    # Assert the customer's eval_input_fn is used correctly
    eval_input_fn = args[0]
    returned_dict, returned_labels = eval_input_fn()
    assert (tensor_dict, labels) == (returned_dict, returned_labels)
    # Assert the created LatestExporter is passed correctly to the EvalSpec
    assert exporter_mock.return_value == kwargs['exporters']
    # Assert the created EvalSpec is returned from _build_eval_spec
    assert evalspec_mock.return_value == spec


def test_build_eval_spec_no_serving(modules, trainer):
    # Set up "customer script".
    tensor_dict = {'inputs': ['faketensor']}
    labels = ['fakelabels']
    expected_hps = trainer.customer_params.copy()
    def customer_eval_input_fn(training_dir, params):
        assert training_dir == TRAIN_DIR
        assert params == expected_hps
        return tensor_dict, labels
    customer_script = EmptyModule()
    customer_script.eval_input_fn = customer_eval_input_fn
    trainer.customer_script = customer_script

    spec = trainer._build_eval_spec()

    evalspec_mock = modules.estimator.EvalSpec
    # eval_steps not specified by customer, use default of 100.
    # serving_input_fn not specified by customer, don't provide an exporter.
    evalspec_mock.assert_called_with(ANY, steps=100, exporters=None)
    args, _ = evalspec_mock.call_args
    # Assert the customer's eval_input_fn is used correctly
    eval_input_fn = args[0]
    returned_dict, returned_labels = eval_input_fn()
    assert (tensor_dict, labels) == (returned_dict, returned_labels)


def test_build_tf_config_with_one_host(trainer):
    trainer.hosts = ['algo-1']
    trainer.current_host = 'algo-1'

    tf_config = trainer.build_tf_config()

    expected_tf_config = {
        'environment': 'cloud',
        'cluster': {
            'master': ['algo-1:2222']
        },
        'task': {'index': 0, 'type': 'master'}
    }

    assert tf_config == expected_tf_config
    assert trainer.task_type == 'master'


def test_build_tf_config_with_multiple_hosts(trainer):
    trainer.hosts = ['algo-1', 'algo-2', 'algo-3', 'algo-4']
    trainer.current_host = 'algo-3'

    tf_config = trainer.build_tf_config()

    expected_tf_config = {
        'environment': 'cloud',
        'cluster': {
            'master': ['algo-1:2222'],
            'ps': ['algo-1:2223', 'algo-2:2223', 'algo-3:2223', 'algo-4:2223'],
            'worker': ['algo-2:2222', 'algo-3:2222', 'algo-4:2222']
        },
        'task': {'index': 1, 'type': 'worker'}
    }

    assert tf_config == expected_tf_config
    assert trainer.task_type == 'worker'


@patch('boto3.client')
@patch('botocore.session.get_session')
@patch('os.environ')
def test_configure_s3_file_system(os_env, botocore, boto_client, trainer_module):
    trainer_module.Trainer(customer_script=MOCK_SCRIPT,
                           current_host=CURRENT_HOST,
                           hosts=HOSTS,
                           model_path='s3://my/s3/path')

    boto_client('s3').get_bucket_location.assert_called_once_with(Bucket='my')

    calls = [
        call('S3_USE_HTTPS', '1'),
        call('S3_REGION', boto_client('s3').get_bucket_location()['LocationConstraint'])
    ]

    os_env.__setitem__.assert_has_calls(calls, any_order=True)