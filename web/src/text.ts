// Every string the screens show, in Brazilian Portuguese: the singers read it.
// The rest of the repository stays in English (CLAUDE.md, Rules).

import type { Entry, EntryState, SessionState } from './api'

export const text = {
  loading: 'Carregando...',
  offline: 'Sem conexão com o servidor do palco. Tentando de novo...',
  detailLabel: 'Detalhe técnico',

  home: {
    nowPlaying: 'Tocando agora',
    current: 'Próxima a cantar',
    ended: 'A apresentação terminou',
    emptyPlaylist: 'A fila está vazia. Busque uma música para começar.',
    goToStage: 'Ir para o palco',
    upcoming: 'Na fila',
    noUpcoming: 'Nenhuma outra música na fila.',
    remove: 'Tirar da fila',
    moveUp: 'Subir na fila',
    moveDown: 'Descer na fila',
    up: '↑',
    down: '↓',
  },

  finder: {
    searchTab: 'Buscar',
    libraryTab: 'Biblioteca',
    searchPlaceholder: 'Nome da música ou do artista',
    searchButton: 'Buscar',
    searching: 'Buscando...',
    noResults: 'Nenhuma música encontrada.',
    offlineNotice: 'Sem internet: só aparecem as músicas que já estão na biblioteca.',
    emptyLibrary: 'A biblioteca está vazia.',
    ready: 'Pronta',
    add: 'Adicionar',
    adding: 'Adicionando...',
    added: 'Na fila',
    noThumbnail: '♪',
    separator: ' · ',
  },

  setup: {
    title: 'Preparar a música',
    backHome: 'Voltar ao início',
    noSong: 'Nenhuma música na fila.',
    pickSong: 'Escolher uma música',
    stems: 'Instrumentos e vozes',
    stemsHint: 'Toque para ligar ou desligar.',
    stemsLater: 'Os instrumentos aparecem quando a música estiver pronta.',
    on: 'Ligado',
    off: 'Desligado',
    key: 'Tom',
    keyDown: 'Baixar o tom',
    keyUp: 'Subir o tom',
    minus: '-',
    plus: '+',
    start: 'Começar',
    starting: 'Começando...',
    playlist: 'Fila',
    skip: 'Pular para a próxima',
    volumes: 'Volume',
  },

  volumes: {
    music: 'Música',
    voice: 'Microfones',
    musicDown: 'Baixar a música',
    musicUp: 'Aumentar a música',
    voiceDown: 'Baixar os microfones',
    voiceUp: 'Aumentar os microfones',
    waiting: 'Os volumes aparecem quando o palco se conectar ao REAPER.',
  },

  playing: {
    // Skipping a song that plays stops it and shows the results.
    skip: 'Pular música',
    skipping: 'Pulando...',
    pause: 'Pausar',
    pausing: 'Pausando...',
    resume: 'Continuar',
    resuming: 'Continuando...',
    noLyrics: 'Esta música não tem letra.',
    lyricsFailed: 'Não foi possível carregar a letra.',
    instrumental: '♪',
    timeOf: ' / ',
  },

  results: {
    ended: 'A apresentação terminou',
    next: 'Próxima música',
    playNow: 'Tocar agora',
    setupFirst: 'Preparar antes',
    noNext: 'Não há mais músicas na fila.',
    backHome: 'Voltar ao início',
  },

  // The public page on a phone (docs/PLAN.md section 5.9).
  public: {
    nowTab: 'Agora',
    queueTab: 'Fila',
    searchTab: 'Buscar',
    offline: 'O karaokê está fora do ar. O que aparece aqui pode estar desatualizado.',
    notLive: 'O karaokê não está no ar agora.',
    library: 'Biblioteca',
    paused: 'Música pausada',
  },
}

/** What the current song is, by the state of the session. */
export const sessionHeadings: Record<SessionState, string> = {
  setup: text.home.current,
  playing: text.home.nowPlaying,
  results: text.home.ended,
}

export const stemNames: Record<string, string> = {
  lead: 'Voz principal',
  backing: 'Vozes de apoio',
  drums: 'Bateria',
  bass: 'Baixo',
  guitar: 'Guitarra',
  piano: 'Piano',
  other: 'Outros',
}

const entryStates: Record<EntryState, string> = {
  queued: 'Aguardando o Studio',
  starting: 'O Studio está começando',
  downloading: 'Baixando a música no Studio',
  separating: 'Separando as faixas',
  lyrics: 'Buscando a letra',
  packaging: 'Empacotando',
  fetching: 'Trazendo para o palco',
  ready: 'Pronta',
  failed: 'Falhou',
}

const separationSteps: Record<string, string> = {
  vocals: 'vozes',
  karaoke: 'voz principal',
  instruments: 'instrumentos',
}

/** What an error code means, for every code of docs/PLAN.md section 5.8. */
export const errorMessages: Record<string, string> = {
  // Why an entry failed, and the warning it may carry while it waits.
  no_studio: 'O palco está sem Studio: só dá para tocar músicas da biblioteca.',
  studio_error: 'O Studio recusou o pedido.',
  job_failed: 'O Studio não conseguiu processar a música.',
  fetch_failed: 'A música não chegou inteira ao palco.',
  internal_error: 'Erro interno do palco.',
  studio_unreachable: 'Sem contato com o Studio. Tentando de novo.',
  // Refusals of the API.
  unknown_entry: 'Essa música não está mais na fila.',
  unknown_song: 'Essa música não está na biblioteca.',
  no_song: 'Não há música na fila.',
  not_ready: 'A música ainda não está pronta.',
  wrong_state: 'Não dá para fazer isso agora.',
  entry_playing: 'A música que está tocando não pode sair da fila. Pule a música antes.',
  entry_current: 'Essa música já é a da vez e não muda de lugar na fila.',
  not_addable: 'Essa música não pode ser adicionada.',
  invalid_request: 'O palco não entendeu o pedido.',
  not_found: 'O palco não conhece esse endereço.',
  method_not_allowed: 'O palco não aceita esse pedido.',
  http_error: 'O palco respondeu com um erro.',
  reaper_unreachable: 'O REAPER não responde. Confira se ele está aberto.',
  listener_silent: 'O script do karaokê no REAPER não respondeu. Reinicie o REAPER.',
  reaper_failed: 'O REAPER não conseguiu fazer o que foi pedido.',
  // Raised by the browser itself.
  network: 'Sem conexão com o servidor do palco.',
}

export function errorMessage(code: string): string {
  return errorMessages[code] ?? 'Algo deu errado.'
}

/**
 * What a phone says where it differs from the TV: the public reaches the
 * Stage through the internet and cannot act on it. The other codes read as on
 * the TV.
 */
const phoneErrorMessages: Record<string, string> = {
  network: 'Sem conexão com o karaokê. Confira a internet do celular e tente de novo.',
  http_error: 'O karaokê respondeu com um erro. Tente de novo.',
  entry_playing: 'Essa música já começou e não pode mais sair da fila.',
  store_unavailable: 'O karaokê está fora do ar agora.',
  expired: 'O pedido demorou demais para chegar ao palco e não foi feito. Tente de novo.',
  no_answer: 'Não foi possível concluir o pedido. Confira a fila antes de tentar de novo.',
  search_no_answer: 'A busca demorou demais. Tente de novo.',
}

export function phoneErrorMessage(code: string): string {
  return phoneErrorMessages[code] ?? errorMessage(code)
}

export function stemName(stem: string): string {
  return stemNames[stem] ?? stem
}

export function entryStateText(entry: Entry): string {
  const state = entryStates[entry.state]
  if (entry.state === 'separating' && entry.step) {
    return `${state}: ${separationSteps[entry.step] ?? entry.step}`
  }
  if (entry.state === 'fetching' && entry.progress) {
    const { files_done, files_total } = entry.progress
    return `${state}: ${files_done} de ${files_total} arquivos`
  }
  if (entry.state === 'failed' && entry.error) {
    return `${state}: ${errorMessage(entry.error)}`
  }
  return state
}

/** A volume in whole dB, with its sign. */
export function volumeText(db: number): string {
  const rounded = Math.round(db)
  return `${rounded > 0 ? '+' : ''}${rounded} dB`
}

export function keyText(key: number): string {
  if (key === 0) return 'Original'
  const steps = Math.abs(key) === 1 ? 'semitom' : 'semitons'
  return `${key > 0 ? '+' : '-'}${Math.abs(key)} ${steps}`
}
