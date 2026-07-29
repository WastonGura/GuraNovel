"""Safe HTTP contracts for the project-creation workflow foundation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonBlankText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
UserSeed = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]
ConceptOptionId = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")
]
ConceptTitle = Annotated[
    str, StringConstraints(min_length=1, max_length=160, pattern=r"^[^\r\n]+$")
]
ConceptLogline = Annotated[
    str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^\r\n]+$")
]
ConceptPremise = Annotated[
    str, StringConstraints(min_length=1, max_length=2_000, pattern=r"^[^\r\n]+$")
]
ConceptGenre = Annotated[
    str, StringConstraints(min_length=1, max_length=256, pattern=r"^[^,\r\n]+$")
]


class StartProjectCreationRequest(BaseModel):
    """Transient creative preferences accepted at the creation boundary only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    user_seed: UserSeed
    target_platform: NonBlankText | None = None
    preferred_genres: list[NonBlankText] | None = Field(default=None, min_length=1, max_length=10)
    disliked_elements: list[NonBlankText] | None = Field(default=None, min_length=1, max_length=10)
    style_preference: NonBlankText | None = None


class ProjectCreationConceptOptionResponse(BaseModel):
    """Allowlisted projection of one server-validated concept option."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: ConceptOptionId
    title: ConceptTitle
    logline: ConceptLogline
    premise: ConceptPremise
    genres: tuple[ConceptGenre, ...] = Field(min_length=1, max_length=6)


class ProjectCreationPendingActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    type: str
    status: Literal["pending"]
    allowed_decisions: tuple[str, ...]
    review_severity: Literal["blocking", "warning", "clean"] | None = None
    concept_options: tuple[ProjectCreationConceptOptionResponse, ...] = Field(
        default_factory=tuple, max_length=5
    )


class ProjectCreationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, from_attributes=True)

    id: UUID
    type: str
    status: str
    current_node: str | None
    next_node: str | None
    awaiting_user: bool
    pending_action: ProjectCreationPendingActionResponse | None


class ResolveProjectCreationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["select", "fuse", "regenerate", "feedback"]
    option_id: Annotated[str, StringConstraints(pattern=r"[a-z][a-z0-9-]{0,63}")] | None = None
    fused_concept: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
        | None
    ) = None
    feedback: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
        | None
    ) = None
