# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Runner registry + loaders. Import side-effects register runners."""

from .base import Runner, answer_matches, register, make, available  # noqa: F401

# Import each runner module so it self-registers. Keep ordering stable.
from . import (  # noqa: E402,F401
    single,
    orchestrator_worker,
    mad,
    agent_storm,
    as_v2,
    as_v3_indfirst,
    as_v3_neutral,
    as_v3_ensemble,
    as_v4_qualsyn,
    as_v5_adaptive,
    as_v5_neutral,
    as_v5_aligned,
    as_v6_safeguard,
    as_v6_evidential,
    as_v7_adaptive,
    as_v8_adaptive,
    as_v8_nopersona,
    as_v9_adaptive,
    as_v9_indonly,
    as_v10_ablation,
    as_v10_constraint_role,
    as_v11_aligned,
    as_v12_stronger_moderator,
    as_v13_nopersona,
    as_v14_aligned_indfirst,
    as_v15_better_fallback,
    as_v16_adaptive_aligned,
    self_consistency,
    multi_prompt_vote,
    multi_persona_single,
    same_persona_vote,
    moa,
)
