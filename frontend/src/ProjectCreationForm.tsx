import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError, startProjectCreation } from './api/client'

interface ProjectCreationFormProps {
  projectId: string
}

export default function ProjectCreationForm({ projectId }: ProjectCreationFormProps) {
  const navigate = useNavigate()
  const [userSeed, setUserSeed] = useState('')
  const [targetPlatform, setTargetPlatform] = useState('')
  const [preferredGenres, setPreferredGenres] = useState('')
  const [dislikedElements, setDislikedElements] = useState('')
  const [stylePreference, setStylePreference] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    if (!userSeed.trim()) {
      setError('请填写创作灵感。')
      return
    }
    setPending(true)
    setError(null)
    try {
      const result = await startProjectCreation(projectId, {
        user_seed: userSeed.trim(),
        target_platform: targetPlatform.trim() || null,
        preferred_genres: preferredGenres
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        disliked_elements: dislikedElements
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        style_preference: stylePreference.trim() || null,
      })
      navigate(`/projects/${projectId}/creation/${result.id}/gate`)
    } catch (caught: unknown) {
      if (caught instanceof ApiError) {
        if (caught.status === 404) {
          setError('项目未找到，请返回项目列表。')
        } else if (caught.status === 409) {
          setError('项目已有活跃的创建工作流。')
        } else if (caught.status === 422) {
          setError('输入信息有误，请检查后重试。')
        } else {
          setError('创建工作流启动失败，请重试。')
        }
      } else {
        setError('创建工作流启动失败，请重试。')
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="workspace-form" onSubmit={submit} aria-label="开始创作">
      <h2>开始创作</h2>

      <label>
        创作灵感
        <textarea
          value={userSeed}
          onChange={(event) => setUserSeed(event.target.value.slice(0, 4000))}
          maxLength={4000}
          rows={6}
          placeholder="描述你的故事灵感、世界观、角色设定……"
        />
        <span className="muted" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
          {userSeed.length} / 4000
        </span>
      </label>

      <div className="form-grid">
        <label>
          目标平台
          <input
            value={targetPlatform}
            onChange={(event) => setTargetPlatform(event.target.value.slice(0, 500))}
            maxLength={500}
            placeholder="例如：起点中文网"
          />
        </label>

        <label>
          风格偏好
          <input
            value={stylePreference}
            onChange={(event) => setStylePreference(event.target.value.slice(0, 500))}
            maxLength={500}
            placeholder="例如：轻松幽默、黑暗史诗"
          />
        </label>
      </div>

      <div className="form-grid">
        <label>
          偏好类型
          <input
            value={preferredGenres}
            onChange={(event) => setPreferredGenres(event.target.value)}
            placeholder="逗号分隔，例如：奇幻, 冒险"
          />
        </label>

        <label>
          不喜欢的元素
          <input
            value={dislikedElements}
            onChange={(event) => setDislikedElements(event.target.value)}
            placeholder="逗号分隔，例如：后宫, 系统流"
          />
        </label>
      </div>

      {error && <p className="notice" role="alert">{error}</p>}
      {error === '项目未找到，请返回项目列表。' && <Link className="back-link" to="/">返回项目列表</Link>}

      <button type="submit" disabled={pending}>
        {pending ? '正在启动创作…' : '开始创作'}
      </button>
    </form>
  )
}
