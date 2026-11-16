// What the phone says for each error: the public cannot act on the Stage or its server.

import { describe, expect, it } from 'vitest'
import { errorMessage, phoneErrorMessage } from '../src/text'

describe('the phone error messages', () => {
  it('blames neither the Stage nor its server for a failed connection', () => {
    for (const code of ['network', 'http_error']) {
      expect(phoneErrorMessage(code)).not.toMatch(/palco/i)
    }
  })

  it('does not ask the public to stop a song that already started', () => {
    expect(phoneErrorMessage('entry_playing')).not.toMatch(/pare/i)
  })

  it('says an expired request was not applied and can be sent again', () => {
    expect(phoneErrorMessage('expired')).not.toMatch(/fila/i)
    expect(phoneErrorMessage('expired')).toMatch(/tente de novo/i)
  })

  it('does not send a search that got no answer to the playlist', () => {
    expect(phoneErrorMessage('search_no_answer')).not.toMatch(/fila/i)
  })

  it('has a message for every code only the phone sees', () => {
    for (const code of ['expired', 'no_answer', 'search_no_answer', 'store_unavailable']) {
      expect(phoneErrorMessage(code)).not.toBe(errorMessage('an unknown code'))
    }
  })

  it('reads as the TV for the other refusals', () => {
    for (const code of ['not_addable', 'unknown_entry', 'invalid_request']) {
      expect(phoneErrorMessage(code)).toBe(errorMessage(code))
    }
  })

  it('leaves the TV messages as they were', () => {
    expect(errorMessage('network')).toBe('Sem conexão com o servidor do palco.')
  })

  it('sends the TV to the skip button, the only way a song that plays leaves', () => {
    expect(errorMessage('entry_playing')).toBe(
      'A música que está tocando não pode sair da fila. Pule a música antes.',
    )
  })
})
