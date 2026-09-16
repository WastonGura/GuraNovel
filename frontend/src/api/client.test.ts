import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  writeStudioDraft,
  getChapterProduction,
  getProjectCreationRun,
  getProject,
  listProjects,
  resolveProjectCreationAction,
  restoreDocument,
  listRestorePoints,
  createRestorePoint,
  restorePoint,
  readStudioFeedback,
  writeStudioFeedback,
  submitStudioFeedback,
  readFeedbackSubmission,
  readRestorePointFeedback,
  getSettingCollection,
  listSettingCollections,
  archiveSettingCollection,
  listCollectionProjects,
  listCollectionDocuments,
  createCollectionDocument,
  patchDocument,
  deleteDocument,
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
  it('saves a draft through its chapter scope without supplying actor or workflow authority', async () => {
    const payload = { content: 'Saved prose', expected_current_version_id: 'version' }
    mockJsonResponse(documentVersion)
    await expect(writeStudioDraft('project', 'chapter', payload)).resolves.toEqual(documentVersion)
    expect(fetch).toHaveBeenCalledWith('/api/v1/projects/project/chapters/chapter/draft/content', expect.objectContaining({ method: 'PUT', body: JSON.stringify(payload) }))
  })
  it('round-trips version-bound comments and immutable feedback submissions through scoped endpoints', async () => {
    const comment = { id: 'comment', start: 2, end: 5, quote: '原文段', text: '修改', color: '#8d9bff', submitted: false, orphaned: false }
    const state = { chapter_id: 'chapter', region: 'draft', document_id: 'document', source_version_id: 'version', revision: 1, comments: [comment], requirements: '要求', read_only: false }
    mockJsonResponse(state)
    await expect(readStudioFeedback('project', 'chapter', 'draft')).resolves.toEqual(state)
    expect(fetch).toHaveBeenLastCalledWith('/api/v1/projects/project/chapters/chapter/feedback/draft', expect.objectContaining({ method: 'GET' }))
    const write = { request_id: 'write', expected_current_version_id: 'version', expected_revision: 0, comments: [comment], requirements: '要求' }
    mockJsonResponse(state)
    await expect(writeStudioFeedback('project', 'chapter', 'draft', write)).resolves.toEqual(state)
    expect(fetch).toHaveBeenLastCalledWith('/api/v1/projects/project/chapters/chapter/feedback/draft', expect.objectContaining({ method: 'PUT', body: JSON.stringify(write) }))
    const submission = { id: 'submission', chapter_id: 'chapter', region: 'draft', document_id: 'document', source_version_id: 'version', feedback_revision: 2, comments: [{ ...comment, submitted: true }], requirements: '要求', created_at: project.created_at }
    mockJsonResponse(submission)
    const submit = { request_id: 'submission', expected_current_version_id: 'version', expected_revision: 1, comment_ids: ['comment'] }
    await expect(submitStudioFeedback('project', 'chapter', 'draft', submit)).resolves.toEqual(submission)
    expect(fetch).toHaveBeenLastCalledWith('/api/v1/projects/project/chapters/chapter/feedback/draft/submissions', expect.objectContaining({ method: 'POST', body: JSON.stringify(submit) }))
    mockJsonResponse(submission)
    await expect(readFeedbackSubmission('project', 'chapter', 'draft', 'submission')).resolves.toEqual(submission)
    mockJsonResponse({ ...state, comments: [comment, comment] })
    await expect(readStudioFeedback('project', 'chapter', 'draft')).rejects.toBeInstanceOf(ApiError)
    mockJsonResponse({ ...state, comments: [{ ...comment, orphaned: 'false' }] })
    await expect(readStudioFeedback('project', 'chapter', 'draft')).rejects.toBeInstanceOf(ApiError)
  })

  it('uses scoped restore-point endpoints and rejects incomplete server point identities', async () => {
    const point = { id: 'point', chapter_id: 'chapter', document_id: 'document', version_id: 'version', summary: '手动存档', created_at: project.created_at }
    const payload = { request_id: 'request', expected_current_version_id: 'version' }
    mockJsonResponse(point)
    await expect(createRestorePoint('project', 'chapter', payload)).resolves.toEqual(point)
    expect(fetch).toHaveBeenLastCalledWith('/api/v1/projects/project/chapters/chapter/restore-points', expect.objectContaining({ method: 'POST', body: JSON.stringify(payload) }))
    mockJsonResponse([point])
    await expect(listRestorePoints('project', 'chapter')).resolves.toEqual([point])
    mockJsonResponse(documentVersion)
    await expect(restorePoint('project', 'chapter', 'point', payload)).resolves.toEqual(documentVersion)
    expect(fetch).toHaveBeenLastCalledWith('/api/v1/projects/project/chapters/chapter/restore-points/point/restore', expect.objectContaining({ method: 'POST', body: JSON.stringify(payload) }))
    mockJsonResponse([{ ...point, version_id: null }])
    await expect(listRestorePoints('project', 'chapter')).rejects.toBeInstanceOf(ApiError)
    const feedback = { point_id: 'point', document_id: 'document', source_version_id: 'version', available: false, comments: [], requirements: '' }
    mockJsonResponse(feedback)
    await expect(readRestorePointFeedback('project', 'chapter', 'point')).resolves.toEqual(feedback)
    expect(fetch).toHaveBeenLastCalledWith('/api/v1/projects/project/chapters/chapter/restore-points/point/feedback', expect.objectContaining({ method: 'GET' }))
    mockJsonResponse({ ...feedback, point_id: 'another-point' })
    await expect(readRestorePointFeedback('project', 'chapter', 'point')).rejects.toBeInstanceOf(ApiError)
    mockJsonResponse({ ...feedback, requirements: 'invented old requirements' })
    await expect(readRestorePointFeedback('project', 'chapter', 'point')).rejects.toBeInstanceOf(ApiError)
  })

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
        blocking_issues: [],
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
        blocking_issues: [],
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
        blocking_issues: [],
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

  it('decodes only allowlisted blocking issues and posts server-authorized regeneration decisions', async () => {
    mockJsonResponse({
      id: 'run-blocked',
      type: 'project_creation',
      status: 'revision_required',
      current_node: 'concept_revision',
      next_node: null,
      awaiting_user: true,
      pending_action: {
        id: 'action-server-id',
        type: 'project_creation_concept_regeneration',
        status: 'pending',
        allowed_decisions: ['regenerate', 'feedback'],
        review_severity: 'blocking',
        blocking_issues: [{
          code: 'premise_conflict',
          message: 'The premise conflicts with the requested tone.',
          raw_report: 'must not survive decoding',
        }],
        concept_options: [],
        review_report_id: 'must-not-survive',
      },
    })

    await expect(getProjectCreationRun('project-1', 'run-blocked')).resolves.toMatchObject({
      pending_action: {
        blocking_issues: [{
          code: 'premise_conflict',
          message: 'The premise conflicts with the requested tone.',
        }],
      },
    })

    mockJsonResponse({ status: 'revision_required' })
    await expect(resolveProjectCreationAction(
      'project-1', 'run-blocked', 'action-server-id', { decision: 'regenerate' },
    )).resolves.toEqual({ status: 'revision_required' })
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/v1/projects/project-1/creation/run-blocked/actions/action-server-id/resolve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ decision: 'regenerate' }),
      }),
    )
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

  it('fetches setting collection by id', async () => {
    const col = {
      id: 'col-1',
      owner_id: 'user-1',
      slug: 'test-col',
      title: 'Test Collection',
      description: 'A collection description',
      status: 'active',
      workspace_root: '/tmp/col-1',
      revision: 1,
      metadata: {},
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    }
    mockJsonResponse(col)
    await expect(getSettingCollection('col-1')).resolves.toEqual(col)
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/col-1', expect.objectContaining({ method: 'GET' }))
  })

  it('lists setting collections with filter query params', async () => {
    mockJsonResponse([])
    await expect(listSettingCollections({ status: 'active', owner_id: 'user-1' })).resolves.toEqual([])
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/setting-collections?status=active&owner_id=user-1',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('archives a setting collection', async () => {
    const archivedCol = {
      id: 'col-1',
      owner_id: 'user-1',
      slug: 'test-col',
      title: 'Test Collection',
      description: null,
      status: 'archived',
      workspace_root: '/tmp/col-1',
      revision: 2,
      metadata: {},
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    }
    mockJsonResponse(archivedCol)
    await expect(archiveSettingCollection('col-1')).resolves.toEqual(archivedCol)
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/col-1/archive', expect.objectContaining({ method: 'POST' }))
  })

  it('lists projects for a setting collection', async () => {
    mockJsonResponse([project])
    await expect(listCollectionProjects('col-1')).resolves.toEqual([project])
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/col-1/projects', expect.objectContaining({ method: 'GET' }))
  })

  it('lists documents for a setting collection', async () => {
    const doc = {
      id: 'doc-1',
      project_id: null,
      setting_collection_id: 'col-1',
      chapter_id: null,
      type: 'character_profile',
      title: 'Alice',
      path: 'characters/alice.md',
      current_version_id: 'ver-1',
      current_version: null,
      metadata: {},
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    }
    mockJsonResponse([doc])
    await expect(listCollectionDocuments('col-1')).resolves.toEqual([doc])
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/col-1/documents', expect.objectContaining({ method: 'GET' }))
  })

  it('creates a setting document in collection', async () => {
    const doc = {
      id: 'doc-1',
      project_id: null,
      setting_collection_id: 'col-1',
      chapter_id: null,
      type: 'character_profile',
      title: 'Bob',
      path: 'characters/bob.md',
      current_version_id: 'ver-1',
      current_version: null,
      metadata: {},
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    }
    mockJsonResponse(doc)
    const payload = {
      type: 'character_profile' as const,
      title: 'Bob',
      path: 'characters/bob.md',
      content: 'Bob info',
    }
    await expect(createCollectionDocument('col-1', payload)).resolves.toEqual(doc)
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/col-1/documents', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(payload),
    }))
  })

  it('patches a document title and metadata', async () => {
    const doc = {
      id: 'doc-1',
      project_id: null,
      setting_collection_id: 'col-1',
      chapter_id: null,
      type: 'character_profile',
      title: 'Bob Renamed',
      path: 'characters/bob.md',
      current_version_id: 'ver-1',
      current_version: null,
      metadata: { role: 'protagonist' },
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    }
    mockJsonResponse(doc)
    const payload = { title: 'Bob Renamed', metadata: { role: 'protagonist' } }
    await expect(patchDocument('doc-1', payload)).resolves.toEqual(doc)
    expect(fetch).toHaveBeenCalledWith('/api/v1/documents/doc-1', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify(payload),
    }))
  })

  it('deletes a document with 204 No Content', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    await expect(deleteDocument('doc-1')).resolves.toBeUndefined()
    expect(fetch).toHaveBeenCalledWith('/api/v1/documents/doc-1', expect.objectContaining({
      method: 'DELETE',
    }))
  })
})
