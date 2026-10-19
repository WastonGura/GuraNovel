export default function SearchTags({ labels, limit = labels.length }: { labels: string[]; limit?: number }) {
  if (!labels.length) return null
  return <span className="search-tags" role="group" aria-label="作品标签">
    {labels.slice(0, limit).map(label => <span key={label} title={label}>{label}</span>)}
    {labels.length > limit && <span title={labels.slice(limit).join('、')}>+{labels.length - limit}</span>}
  </span>
}
