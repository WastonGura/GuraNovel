"""Local deterministic composition; no endpoint, credential, or model is configurable here."""

from app.agents.chief_editor import ChiefEditor
from app.agents.concept_agent import ConceptAgent
from app.agents.contracts import ConceptAgentRequest, ConceptGenerationOutput


class _DeterministicConceptProvider:
    async def generate_concepts(self, request: ConceptAgentRequest, profile: object) -> object:
        return {
            "options": [
                {
                    "id": "story-spark",
                    "title": "Story Spark",
                    "logline": "A protagonist confronts a defining change.",
                    "premise": "A focused premise built for an author to develop.",
                    "genres": ["fiction"],
                }
            ]
        }


class _DeterministicChiefEditorProvider:
    async def review_concepts(self, concepts: ConceptGenerationOutput, profile: object) -> object:
        return {"passed": True, "blocking_issues": [], "warnings": [], "notes": []}


class ProjectCreationComposition:
    def __init__(
        self, concept_agent: ConceptAgent | None = None, chief_editor: ChiefEditor | None = None
    ) -> None:
        self.concept_agent = concept_agent or ConceptAgent(_DeterministicConceptProvider())
        self.chief_editor = chief_editor or ChiefEditor(_DeterministicChiefEditorProvider())
