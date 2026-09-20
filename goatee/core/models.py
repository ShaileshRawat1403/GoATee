"""Core domain models for GoATee v0.1 Phase 1."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Project:
    project_name: str
    tech_stack: str = ""
    verification_commands: dict[str, str] = field(default_factory=dict)
    permanent_constraints: list[str] = field(default_factory=list)


@dataclass
class AcceptanceCriterion:
    id: str
    description: str
    evidence_type: str = "automated"  # automated | manual
    command: Optional[str] = None
    expected: Optional[str] = None
    instruction: Optional[str] = None


@dataclass
class BuildBudget:
    expected_surface: list[str] = field(default_factory=list)
    reuse_strategy: list[str] = field(default_factory=list)
    dependency_policy: str = ""
    blast_radius: list[str] = field(default_factory=list)


@dataclass
class ContractState:
    agreement_status: str = "DRAFT"
    budget_status: str = "DRAFT"
    baseline_commit: Optional[str] = None


@dataclass
class ChangeContract:
    change_id: str
    intent: str = ""
    agreed_scope_in: list[str] = field(default_factory=list)
    agreed_scope_out: list[str] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    build_budget: Optional[BuildBudget] = None
    contract_state: ContractState = field(default_factory=ContractState)
