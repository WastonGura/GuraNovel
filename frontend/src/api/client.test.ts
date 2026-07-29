import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  getChapterProduction,
  getProjectCreationRun,
  getProject,
  listProjects,
  restoreDocument,
} from './client'

const project = {
  id: 'project-1',
  slug: 'archive-of-ash',
  title: 'Archive of Ash',
  genre: null,
  target_platform: null,
  status: 'draft',
  workspace_root: '/workspace/archive-of-ash',
  metadata: {},
  created_at: '2026-07-19T00:00:00Z',
  updated_at: '2026-07-19T00:00:00Z',
}

const documentVersion = {
  id: 'version-1',
  document_id: 'document-1',
  version_number: 2,
  parent_version_id: 'version-0',
  source: 'user',
  actor_user_id: null,
  agent_role: null,
  workflow_run_id: null,
  content_hash: 'abc123',
  byte_size: 42,
  word_count: 7,
  file_path: 'documents/document-1.md',
  change_summary: null,
  created_at: '2026-07-19T00:00:00Z',
}

function mockJsonResponse(body: unknown, status = 200): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('typed API client', () => {
  it('decodes a valid project response into the typed contract', async () => {
    mockJsonResponse(project)

    await expect(getProject('project-1')).resolves.toEqual(project)
  })

  it('decodes only allowlisted structured concept options from a project creation run', async () => {
    mockJsonResponse({
      id: 'run-1',
      type: 'project_creation',
      status: 'concept_options',
      current_node: 'concept_review',
      next_node: null,
      awaiting_user: true,
      pending_action: {
        id: 'action-server-id',
        type: 'project_creation_concept_selection',
        status: 'pending',
        allowed_decisions: ['select', 'fuse'],
        review_severity: 'clean',
        concept_options: [{
          id: 'glass-archive',
          title: 'The Glass Archive',
          logline: 'An archivist discovers a city preserved in glass.',
          premise: 'Every recovered memory changes the city that contains it.',
          genres: ['fantasy', 'mystery'],
          provider_payload: 'must not survive decoding',
        }],
        concept_document_id: 'must-not-survive',
      },
    })

    await expect(getProjectCreationRun('project-1', 'run-1')).resolves.toEqual({
      id: 'run-1',
      type: 'project_creation',
      status: 'concept_options',
      current_node: 'concept_review',
      next_node: null,
      awaiting_user: true,
      pending_action: {
        id: 'action-server-id',
        type: 'project_creation_concept_selection',
        status: 'pending',
        allowed_decisions: ['select', 'fuse'],
        review_severity: 'clean',
        concept_options: [{
          id: 'glass-archive',
          title: 'The Glass Archive',
          logline: 'An archivist discovers a city preserved in glass.',
          premise: 'Every recovered memory changes the city that contains it.',
          genres: ['fantasy', 'mystery'],
        }],
      },
    })
  })

  it('accepts concept option strings at the backend Unicode code-point limit', async () => {
    const title = '😀'.repeat(160)
    mockJsonResponse({
      id: 'run-emoji',
      type: 'project_creation',
      status: 'concept_options',
      current_node: 'concept_review',
      next_node: null,
      awaiting_user: true,
      pending_action: {
        id: 'action-server-id',
        type: 'project_creation_concept_selection',
        status: 'pending',
        allowed_decisions: ['select'],
        review_severity: 'clean',
        concept_options: [{
          id: 'emoji-concept',
          title,
          logline: 'A valid server concept option.',
          premise: 'The frontend must apply the same Unicode length semantics as the backend.',
          genres: ['fantasy'],
        }],
      },
    })

    await expect(getProjectCreationRun('project-1', 'run-emoji')).resolves.toMatchObject({
      pending_action: { concept_options: [{ title }] },
    })
  })

  it('maps a backend 409 envelope to a safe ApiError without details', async () => {
    mockJsonResponse(
      {
        error: {
          code: 'workflow_state_error',
          message: 'The workflow is no longer awaiting approval.',
          details: { internal_state: 'secret' },
        },
      },
      409,
    )

    try {
      await listProjects()
      throw new Error('Expected listProjects to reject.')
    } catch (error: unknown) {
      expect(error).toBeInstanceOf(ApiError)
      expect(error).toMatchObject({
        name: 'ApiError',
        status: 409,
        code: 'workflow_state_error',
        message: 'The workflow is no longer awaiting approval.',
      })
      expect(error).not.toHaveProperty('details')
    }
  })

  it('uses a generic safe ApiError for a malformed error envelope', async () => {
    mockJsonResponse({ unexpected: 'response data' }, 500)

    await expect(listProjects()).rejects.toMatchObject({
      name: 'ApiError',
      status: 500,
      code: 'request_failed',
      message: 'The request could not be completed.',
    })
  })

  it('fails closed when a production event has an unsafe payload', async () => {
    mockJsonResponse({
      id: 'run-1',
      type: 'chapter_production',
      status: 'awaiting_approval',
      current_node: 'approval',
      next_node: null,
      awaiting_user: true,
      actions: [],
      events: [
        {
          event_type: 'generation_provenance',
          node_name: null,
          message: null,
          payload: { provider_kind: 'fake', raw_output: 'must not be exposed' },
        },
      ],
      outline_document_id: null,
      draft_document_id: null,
    })

    await expect(getChapterProduction('project-1', 'chapter-1', 'run-1')).rejects.toThrow(
      'The server returned an invalid response.',
    )
  })

  it('keeps generated paths under the configured API base path', async () => {
    mockJsonResponse(project)

    await getProject('../outside?redirect=https://invalid.example')

    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/projects/..%2Foutside%3Fredirect%3Dhttps%3A%2F%2Finvalid.example',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it.each([
    '/api/%2e/outside',
    '/api/%2E/outside',
    '/api/%2e%2e/outside',
    '/api/%2E%2E/outside',
    '/api/%2foutside',
    '/api/%5coutside',
    '/api/./outside',
    '/api/../outside',
    '/api/v1?redirect=/outside',
    '/api/v1#outside',
    '//invalid.example/api',
    'https://invalid.example/api',
  ])('rejects unsafe configured API base %s before invoking fetch', async (base) => {
    vi.stubEnv('VITE_API_BASE_URL', base)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(getProject('project-1')).rejects.toMatchObject({
      name: 'ApiError',
      code: 'invalid_response',
    })

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('posts a restore request to the encoded document version route', async () => {
    mockJsonResponse(documentVersion)
    const payload = {
      expected_current_version_id: 'current-version-1',
      source: 'user' as const,
      actor_user_id: 'actor-1',
      agent_role: 'reviewer',
      workflow_run_id: 'workflow-1',
      change_summary: 'Restore approved version',
    }

    await expect(restoreDocument('document/one', 'version?two', payload)).resolves.toEqual(documentVersion)

    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/documents/document%2Fone/versions/version%3Ftwo/restore',
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  })
})
