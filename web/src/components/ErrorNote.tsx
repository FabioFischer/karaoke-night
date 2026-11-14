import { errorMessage, text } from '../text'

interface Props {
  code: string
  detail: string | null
  warning?: boolean
}

/** An error in Portuguese, with the English detail small under it for whoever runs the night. */
export function ErrorNote({ code, detail, warning = false }: Props) {
  return (
    <div className={warning ? 'note note-warning' : 'note note-error'} role="alert">
      <p>{errorMessage(code)}</p>
      {detail && (
        <p className="detail" title={text.detailLabel}>
          {detail}
        </p>
      )}
    </div>
  )
}
