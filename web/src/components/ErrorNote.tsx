import { createContext, useContext } from 'react'
import { errorMessage, text } from '../text'

/** How errors read: the TV shows the English detail for whoever runs the night; a phone does not. */
export interface ErrorWording {
  message: (code: string) => string
  showDetail: boolean
}

export const ErrorWordingContext = createContext<ErrorWording>({
  message: errorMessage,
  showDetail: true,
})

interface Props {
  code: string
  detail: string | null
  warning?: boolean
}

/** An error in Portuguese, with the English detail small under it for whoever runs the night. */
export function ErrorNote({ code, detail, warning = false }: Props) {
  const wording = useContext(ErrorWordingContext)
  return (
    <div className={warning ? 'note note-warning' : 'note note-error'} role="alert">
      <p>{wording.message(code)}</p>
      {wording.showDetail && detail && (
        <p className="detail" title={text.detailLabel}>
          {detail}
        </p>
      )}
    </div>
  )
}
