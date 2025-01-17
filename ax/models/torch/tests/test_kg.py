#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from unittest import mock

import torch
from ax.core.search_space import SearchSpaceDigest
from ax.models.torch.botorch_modular.acquisition import Acquisition
from ax.models.torch.botorch_modular.kg import (
    KnowledgeGradient,
    MultiFidelityKnowledgeGradient,
    OneShotAcquisition,
)
from ax.models.torch.botorch_modular.surrogate import Surrogate
from ax.utils.common.constants import Keys
from ax.utils.common.testutils import TestCase
from botorch.acquisition.knowledge_gradient import qKnowledgeGradient
from botorch.models.gp_regression import SingleTaskGP
from botorch.utils.containers import TrainingData


ACQUISITION_PATH = f"{Acquisition.__module__}"
KG_PATH = f"{KnowledgeGradient.__module__}"


class AcquisitionSetUp:
    def setUp(self):
        self.botorch_model_class = SingleTaskGP
        self.surrogate = Surrogate(botorch_model_class=self.botorch_model_class)
        self.X = torch.tensor([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]])
        self.Y = torch.tensor([[3.0], [4.0]])
        self.Yvar = torch.tensor([[0.0], [2.0]])
        self.training_data = TrainingData(X=self.X, Y=self.Y, Yvar=self.Yvar)
        self.fidelity_features = [2]
        self.surrogate.construct(
            training_data=self.training_data, fidelity_features=self.fidelity_features
        )
        self.search_space_digest = SearchSpaceDigest(
            feature_names=["a", "b", "c"],
            bounds=[(0.0, 10.0), (0.0, 10.0), (0.0, 10.0)],
            target_fidelities={2: 1.0},
        )
        self.botorch_acqf_class = qKnowledgeGradient
        self.objective_weights = torch.tensor([1.0])
        self.pending_observations = [
            torch.tensor([[1.0, 3.0, 4.0]]),
            torch.tensor([[2.0, 6.0, 8.0]]),
        ]
        self.outcome_constraints = (torch.tensor([[1.0]]), torch.tensor([[0.5]]))
        self.linear_constraints = None
        self.fixed_features = {1: 2.0}
        self.options = {
            Keys.FIDELITY_WEIGHTS: {2: 1.0},
            Keys.COST_INTERCEPT: 1.0,
            Keys.NUM_TRACE_OBSERVATIONS: 0,
        }
        self.optimizer_options = {
            Keys.NUM_RESTARTS: 40,
            Keys.RAW_SAMPLES: 1024,
            Keys.FRAC_RANDOM: 0.2,
        }
        self.inequality_constraints = [
            (torch.tensor([0, 1]), torch.tensor([-1.0, 1.0]), 1)
        ]


class OneShotAcquisitionTest(AcquisitionSetUp, TestCase):
    def setUp(self):
        super().setUp()
        self.acquisition = OneShotAcquisition(
            surrogate=self.surrogate,
            search_space_digest=self.search_space_digest,
            objective_weights=self.objective_weights,
            botorch_acqf_class=self.botorch_acqf_class,
            pending_observations=self.pending_observations,
            outcome_constraints=self.outcome_constraints,
            linear_constraints=self.linear_constraints,
            fixed_features=self.fixed_features,
            options=self.options,
        )

    @mock.patch(
        f"{KG_PATH}.gen_one_shot_kg_initial_conditions",
        return_value=torch.tensor([1.0]),
    )
    @mock.patch(f"{ACQUISITION_PATH}.Acquisition.optimize")
    def test_optimize(self, mock_parent_optimize, mock_init_conditions):
        self.acquisition.optimize(
            n=1,
            search_space_digest=self.search_space_digest,
            optimizer_class=None,
            inequality_constraints=self.inequality_constraints,
            fixed_features=self.fixed_features,
            rounding_func="func",
            optimizer_options=self.optimizer_options,
        )
        mock_init_conditions.assert_called_with(
            acq_function=mock.ANY,
            bounds=mock.ANY,
            q=1,
            num_restarts=self.optimizer_options[Keys.NUM_RESTARTS],
            raw_samples=self.optimizer_options[Keys.RAW_SAMPLES],
            options={
                Keys.FRAC_RANDOM: self.optimizer_options[Keys.FRAC_RANDOM],
                Keys.NUM_INNER_RESTARTS: self.optimizer_options[Keys.NUM_RESTARTS],
                Keys.RAW_INNER_SAMPLES: self.optimizer_options[Keys.RAW_SAMPLES],
            },
        )
        # can't use assert_called_with on bounds due to ambiguous bool comparison
        expected_bounds = torch.tensor(
            self.search_space_digest.bounds,
            dtype=self.acquisition.dtype,
            device=self.acquisition.device,
        ).transpose(0, 1)
        self.assertTrue(
            torch.equal(mock_init_conditions.call_args[1]["bounds"], expected_bounds)
        )
        self.optimizer_options[Keys.BATCH_INIT_CONDITIONS] = torch.tensor([1.0])
        # `OneShotAcquisition.optimize()` should call `Acquisition.optimize()` once.
        mock_parent_optimize.assert_called_once_with(
            n=1,
            search_space_digest=self.search_space_digest,
            inequality_constraints=self.inequality_constraints,
            fixed_features=self.fixed_features,
            rounding_func="func",
            optimizer_options=self.optimizer_options,
        )


class KnowledgeGradientTest(AcquisitionSetUp, TestCase):
    def setUp(self):
        super().setUp()
        self.acquisition = KnowledgeGradient(
            surrogate=self.surrogate,
            search_space_digest=self.search_space_digest,
            objective_weights=self.objective_weights,
            botorch_acqf_class=self.botorch_acqf_class,
            pending_observations=self.pending_observations,
            outcome_constraints=self.outcome_constraints,
            linear_constraints=self.linear_constraints,
            fixed_features=self.fixed_features,
            options=self.options,
        )

    @mock.patch(
        f"{ACQUISITION_PATH}.Acquisition.compute_model_dependencies", return_value={}
    )
    def test_compute_model_dependencies(self, mock_Acquisition_compute):
        # `KnowledgeGradient.compute_model_dependencies` should call
        # `Acquisition.compute_model_dependencies` once.
        dependencies = self.acquisition.compute_model_dependencies(
            surrogate=self.surrogate,
            search_space_digest=self.search_space_digest,
            objective_weights=self.objective_weights,
        )
        mock_Acquisition_compute.assert_called_once()
        self.assertEqual(dependencies, {})

    @mock.patch(f"{KG_PATH}.OneShotAcquisition.optimize")
    def test_optimize(self, mock_OneShot_optimize):
        # `KnowledgeGradient.optimize()` should call `OneShotAcquisition.optimize()`
        # once.
        self.acquisition.optimize(
            n=1,
            search_space_digest=self.search_space_digest,
            optimizer_class=None,
            inequality_constraints=self.inequality_constraints,
            fixed_features=self.fixed_features,
            rounding_func="func",
            optimizer_options=self.optimizer_options,
        )
        mock_OneShot_optimize.assert_called_once()


class MultiFidelityKnowledgeGradientTest(AcquisitionSetUp, TestCase):
    def setUp(self):
        super().setUp()
        self.acquisition = MultiFidelityKnowledgeGradient(
            surrogate=self.surrogate,
            search_space_digest=self.search_space_digest,
            objective_weights=self.objective_weights,
            botorch_acqf_class=self.botorch_acqf_class,
            pending_observations=self.pending_observations,
            outcome_constraints=self.outcome_constraints,
            linear_constraints=self.linear_constraints,
            fixed_features=self.fixed_features,
            options=self.options,
        )

    @mock.patch(
        f"{ACQUISITION_PATH}.Acquisition.compute_model_dependencies", return_value={}
    )
    def test_compute_model_dependencies(self, mock_Acquisition_compute):
        # `MultiFidelityKnowledgeGradient.compute_model_dependencies` should
        # call `Acquisition.compute_model_dependencies` once.
        dependencies = self.acquisition.compute_model_dependencies(
            surrogate=self.surrogate,
            search_space_digest=self.search_space_digest,
            objective_weights=self.objective_weights,
            pending_observations=self.pending_observations,
            outcome_constraints=self.outcome_constraints,
            linear_constraints=self.linear_constraints,
            fixed_features=self.fixed_features,
            options=self.options,
        )
        mock_Acquisition_compute.assert_called_once()
        # Dependencies list should have `Keys.CURRENT_VALUE` in it
        self.assertTrue(Keys.CURRENT_VALUE in dependencies)

    @mock.patch(f"{KG_PATH}.OneShotAcquisition.optimize")
    def test_optimize(self, mock_OneShot_optimize):
        # `MultiFidelityKnowledgeGradient.optimize()` should call
        # `OneShotAcquisition.optimize()` once.
        self.acquisition.optimize(
            n=1,
            search_space_digest=self.search_space_digest,
            optimizer_class=None,
            inequality_constraints=self.inequality_constraints,
            fixed_features=self.fixed_features,
            rounding_func="func",
            optimizer_options=self.optimizer_options,
        )
        mock_OneShot_optimize.assert_called_once()
