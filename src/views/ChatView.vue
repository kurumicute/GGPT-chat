<script setup>
import { ref, shallowRef, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const authView = ref(true)
const loading = ref(false)
const errorMessage = ref('')

const shareMatch = window.location.pathname.match(
  /^\/c\/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\/?$/i
)
const pendingShareToken = shareMatch?.[1] || ''
let linkedSharedConversationId = null
const shareModalOpen = ref(false)
const shareLoading = ref(false)
const shareLink = ref('')
const shareCopied = ref(false)
const shareError = ref('')

const username = ref('')
const password = ref('')
const accountName = ref('')

const COMMON_USERNAMES = new Set([
  'admin', 'administrator', 'root', 'system', 'user', 'username',
  'test', 'tester', 'demo', 'guest', 'support', 'webmaster',
  '管理員', '系統', '測試', '訪客'
])
const COMMON_PASSWORDS = new Set([
  '12345678', '123456789', '1234567890', '00000000', '11111111',
  'password', 'password1', 'passw0rd', 'qwerty123', 'qwertyuiop',
  'abc12345', 'admin123', 'administrator', 'iloveyou', 'letmein',
  'welcome1', 'changeme', 'test1234', 'user1234'
])

function registrationValidationError() {
  const account = username.value.normalize('NFKC').trim()
  const secret = password.value

  if (account.length < 3 || account.length > 32) {
    return '帳號長度需為 3～32 個字元'
  }
  if (!/^[\p{L}\p{N}_.-]+$/u.test(account) ||
      !/^[\p{L}\p{N}]/u.test(account) ||
      !/[\p{L}\p{N}]$/u.test(account)) {
    return '帳號只能使用中英文字、數字、底線、句點與連字號，且開頭結尾須為文字或數字'
  }
  if (COMMON_USERNAMES.has(account.toLowerCase())) {
    return '此帳號過於常見，請換一個較不容易猜到的帳號'
  }
  if (secret.length < 8 || secret.length > 128) {
    return '密碼長度需為 8～128 個字元'
  }
  const simplifiedSecret = secret.toLowerCase().replace(/[^a-z0-9]/g, '')
  const simplifiedAccount = account.toLowerCase().replace(/[^a-z0-9]/g, '')
  if (COMMON_PASSWORDS.has(secret.toLowerCase()) ||
      COMMON_PASSWORDS.has(simplifiedSecret)) {
    return '這組密碼太常見，請換一組較不容易猜到的密碼'
  }
  if (simplifiedSecret && simplifiedSecret === simplifiedAccount) {
    return '密碼不能與帳號相同'
  }
  if (new Set(secret).size === 1) {
    return '密碼不能全部使用相同字元'
  }
  return ''
}

// 這些集合都以整批資料更新；shallowRef 可避免數百筆內容被深層 Proxy 化。
const conversations = shallowRef([])
const currentConversationId = ref(null)

const messages = shallowRef([])

const messageInput = ref('')
const composerTextarea = ref(null)
const waiting = ref(false)
const thinkingLabel = ref('正在思考…')

const model = ref(
  localStorage.getItem('ggpt_selected_model_v2') ||
  'gpt-5.6-luna'
)

const webSearchEnabled = ref(
  localStorage.getItem('ggpt_web_search_enabled_v1') === 'true'
)

const reasoningEnabled = ref(
  localStorage.getItem('ggpt_reasoning_enabled_v1') !== 'false'
)

const reasoningEffort = ref(
  localStorage.getItem('ggpt_reasoning_effort_v1') || 'medium'
)

const REASONING_MODELS = new Set([
  'gpt-6-astra',
  'gpt-5.6-sol',
  'gpt-5.6-terra',
  'gpt-5.6-luna'
])

const MODEL_REASONING_EFFORTS = {
  'gpt-6-astra': ['low', 'medium', 'high', 'xhigh', 'max'],
  'gpt-5.6-sol': ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
  'gpt-5.6-terra': ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
  'gpt-5.6-luna': ['none', 'low', 'medium', 'high', 'xhigh', 'max']
}

const modelPopoverOpen = ref(false)
const sidebarOpen = ref(false)

const pendingImage = ref(null)
const pendingFiles = shallowRef([])
const draggingFiles = ref(false)
const uploading = ref(false)
const localObjectUrls = new Set()
const markdownCache = new Map()

const globalChatOpen = ref(false)
const globalChatMessages = shallowRef([])
const globalChatInput = ref('')
const globalChatLoading = ref(false)
const globalChatSending = ref(false)
const globalChatError = ref('')
let globalChatTimer = null
let sharedConversationTimer = null
let lastConversationRecordId = null
let codeRenderObserver = null
let codeRenderRefreshQueued = false
let richTextLoader = null
let markedParser = null
let htmlSanitizer = null
let katexRenderer = null
let syntaxHighlighter = null
const rendererVersion = ref(0)

async function ensureRichTextRenderer() {
  if (markedParser && htmlSanitizer && katexRenderer && syntaxHighlighter) return
  if (!richTextLoader) {
    richTextLoader = Promise.all([
      import('marked'),
      import('dompurify'),
      import('katex'),
      import('highlight.js'),
      import('katex/dist/katex.min.css'),
      import('highlight.js/styles/github-dark.css')
    ]).then(([markedModule, purifyModule, katexModule, highlightModule]) => {
      markedParser = markedModule.marked
      htmlSanitizer = purifyModule.default
      katexRenderer = katexModule.default
      syntaxHighlighter = highlightModule.default
      markdownCache.clear()
      rendererVersion.value += 1
    })
  }
  return richTextLoader
}

const usage = ref(null)
const usageModal = ref(false)

const googleEnabled = ref(false)
const googleClientId = ref('')
const googleReady = ref(false)

const MODEL_OPTIONS = [
  {
    id: 'gpt-6-astra',
    desc: '高階通用模型',
    pricing: '$10 / $1 / $50'
  },
  {
    id: 'gpt-5.6-sol',
    desc: '通用對話模型',
    pricing: '$4 / $0.40 / $20'
  },
  {
    id: 'gpt-5.6-terra',
    desc: '推理與程式能力模型',
    pricing: '$2 / $0.20 / $12'
  },
  {
    id: 'gpt-5.6-luna',
    desc: '通用 AI 助手模型',
    pricing: '$0.20 / $0.02 / $1.20'
  },
  {
    id: 'gpt-5-nano',
    desc: '超低成本、快速模型（不支援思考模式）',
    pricing: '$0.05 / $0.005 / $0.40'
  }
]

const currentConversation = computed(() =>
  conversations.value.find(
    x => x.id === currentConversationId.value
  )
)

const userInitial = computed(() => {
  const name = String(accountName.value || 'U').trim()
  return [...name][0]?.toUpperCase() || 'U'
})

const totalUsage = computed(() => usage.value?.total || {})
const monthUsage = computed(() => usage.value?.month || {})
const webSearchUsage = computed(() => {
  const total = totalUsage.value || {}
  const source =
    usage.value?.web_search ||
    usage.value?.webSearch ||
    usage.value?.search ||
    {}

  return {
    calls: Number(
      source.calls ??
      source.web_search_calls ??
      total.web_search_calls ??
      usage.value?.web_search_calls ??
      0
    ),
    cost: Number(
      source.cost ??
      source.web_search_cost ??
      total.web_search_cost ??
      usage.value?.web_search_cost ??
      0
    ),
    requests: Number(
      source.requests ??
      source.web_search_requests ??
      total.web_search_requests ??
      0
    )
  }
})

const modelUsageRows = computed(() => {
  const raw = usage.value?.models || usage.value?.by_model || usage.value?.model_usage || usage.value?.modelUsage || []
  if (Array.isArray(raw)) {
    return raw.map(item => ({
      model: item.model || item.name || item.id || '未知模型',
      tokens: Number(item.total_tokens ?? item.tokens ?? 0),
      reasoningTokens: Number(item.reasoning_tokens ?? item.reasoningTokens ?? 0),
      reasoningCost: Number(item.reasoning_cost ?? item.reasoningCost ?? 0),
      cost: Number(
        item.model_token_cost ??
        item.token_cost ??
        Math.max(
          0,
          Number(item.total_cost ?? item.cost ?? 0) -
          Number(item.web_search_cost ?? 0)
        )
      ),
      requests: Number(item.requests ?? item.api_requests ?? 0)
    })).filter(item => item.tokens || item.cost || item.requests)
  }
  if (raw && typeof raw === 'object') {
    return Object.entries(raw).map(([name, item]) => {
      const value = typeof item === 'number' ? { total_tokens: item } : (item || {})
      return {
        model: name,
        tokens: Number(value.total_tokens ?? value.tokens ?? 0),
        reasoningTokens: Number(value.reasoning_tokens ?? value.reasoningTokens ?? 0),
        reasoningCost: Number(value.reasoning_cost ?? value.reasoningCost ?? 0),
        // 後端的 total_cost 是「模型 + Web Search」總成本；
        // 這裡優先讀 model_token_cost，沒有時才從 total_cost 扣除 web_search_cost。
        cost: Number(
          value.model_token_cost ??
          value.token_cost ??
          Math.max(
            0,
            Number(value.total_cost ?? value.cost ?? 0) -
            Number(value.web_search_cost ?? 0)
          )
        ),
        requests: Number(value.requests ?? value.api_requests ?? 0)
      }
    }).filter(item => item.tokens || item.cost || item.requests)
  }
  return []
})

function setAccountDisplayName(value) {
  const name = String(value || '').trim()
  if (!name) return

  accountName.value = name
  localStorage.setItem('ggpt_account_name', name)
}

function savePreferences() {
  localStorage.setItem(
    'ggpt_selected_model_v2',
    model.value
  )

  localStorage.setItem(
    'ggpt_web_search_enabled_v1',
    String(webSearchEnabled.value)
  )

  localStorage.setItem(
    'ggpt_reasoning_enabled_v1',
    String(reasoningEnabled.value)
  )

  localStorage.setItem(
    'ggpt_reasoning_effort_v1',
    reasoningEffort.value
  )
}

function modelSupportsReasoning(id = model.value) {
  return REASONING_MODELS.has(id)
}

function availableReasoningEfforts(id = model.value) {
  return MODEL_REASONING_EFFORTS[id] || []
}

function normalizeReasoningPreference() {
  if (!modelSupportsReasoning()) {
    reasoningEnabled.value = false
    return
  }

  const efforts = availableReasoningEfforts()
  if (!efforts.includes(reasoningEffort.value)) {
    reasoningEffort.value = efforts.includes('medium')
      ? 'medium'
      : efforts[0] || 'medium'
  }
}

function selectModel(id) {
  model.value = id
  normalizeReasoningPreference()
  savePreferences()
  modelPopoverOpen.value = false
}

function toggleReasoning() {
  if (!modelSupportsReasoning()) {
    reasoningEnabled.value = false
    return
  }

  reasoningEnabled.value = !reasoningEnabled.value
  savePreferences()
}

function setReasoningEffort(value) {
  if (!modelSupportsReasoning()) return
  if (!availableReasoningEfforts().includes(value)) return
  reasoningEffort.value = value
  reasoningEnabled.value = true
  savePreferences()
}

function toggleWebSearch() {
  webSearchEnabled.value = !webSearchEnabled.value
  savePreferences()
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'include',
    ...options
  })

  let data

  const contentType =
    response.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    data = await response.json()
  } else {
    data = await response.text()
  }

  if (!response.ok) {
    const error = new Error(
      typeof data === 'object'
        ? data.error || `HTTP ${response.status}`
        : data || `HTTP ${response.status}`
    )
    error.status = response.status
    throw error
  }

  return data
}

function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    char => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    })[char]
  )
}

function normalizeMath(text) {
  return String(text ?? '')
    .replace(/\\_/g, '_')
    .replace(/\\=/g, '=')
}

function renderMarkdown(text) {
  // 讓動態載入完成後觸發一次 Vue 更新。
  void rendererVersion.value
  const key = String(text ?? '')

  if (!markedParser || !htmlSanitizer) {
    void ensureRichTextRenderer()
    return `<p>${escapeHtml(key).replace(/\n/g, '<br>')}</p>`
  }

  const cached = markdownCache.get(key)
  if (cached !== undefined) return cached

  try {
    const normalized = normalizeMath(key)

    const html = markedParser.parse(normalized, {
      breaks: true,
      gfm: true
    })

    const result = htmlSanitizer.sanitize(html, {
      USE_PROFILES: {
        html: true
      },
      ADD_ATTR: ['target', 'rel']
    })

    // 避免聊天元件重新 render 時重複跑 marked + DOMPurify。
    if (markdownCache.size > 200) {
      const first = markdownCache.keys().next().value
      markdownCache.delete(first)
    }
    markdownCache.set(key, result)
    return result
  } catch {
    return escapeHtml(key)
  }
}

// 只有真正的「程式碼檔案」顯示下載；shell / config / 純文字維持只有複製。
const DOWNLOADABLE_CODE_LANGUAGES = new Set([
  'js', 'javascript', 'mjs', 'cjs',
  'ts', 'typescript',
  'jsx', 'tsx',
  'vue', 'svelte', 'astro',
  'html', 'htm', 'xml',
  'css', 'scss', 'sass', 'less',
  'py', 'python', 'pyw', 'pyi',
  'java', 'kt', 'kotlin', 'kts',
  'c', 'h', 'cpp', 'c++', 'cc', 'cxx', 'hpp',
  'cs', 'csharp',
  'go', 'golang',
  'rust', 'rs',
  'swift', 'dart', 'lua', 'r',
  'ruby', 'rb', 'php', 'perl', 'pl',
  'sql', 'graphql', 'gql'
])

const CODE_FILE_EXTENSIONS = {
  javascript: 'js', js: 'js', mjs: 'mjs', cjs: 'cjs',
  typescript: 'ts', ts: 'ts',
  jsx: 'jsx', tsx: 'tsx',
  vue: 'vue', svelte: 'svelte', astro: 'astro',
  html: 'html', htm: 'html', xml: 'xml',
  css: 'css', scss: 'scss', sass: 'sass', less: 'less',
  python: 'py', py: 'py', pyw: 'pyw', pyi: 'pyi',
  java: 'java', kt: 'kt', kotlin: 'kt', kts: 'kts',
  c: 'c', h: 'h', cpp: 'cpp', 'c++': 'cpp', cc: 'cc', cxx: 'cxx', hpp: 'hpp',
  cs: 'cs', csharp: 'cs', go: 'go', golang: 'go', rust: 'rs', rs: 'rs',
  swift: 'swift', dart: 'dart', lua: 'lua', r: 'r', ruby: 'rb', rb: 'rb',
  php: 'php', perl: 'pl', pl: 'pl', sql: 'sql', graphql: 'graphql', gql: 'gql'
}

function normalizeCodeLanguage(language) {
  return String(language || '').trim().toLowerCase()
}

function canDownloadCode(language) {
  return DOWNLOADABLE_CODE_LANGUAGES.has(normalizeCodeLanguage(language))
}

function getCodeExtension(language) {
  const key = normalizeCodeLanguage(language)
  return CODE_FILE_EXTENSIONS[key] || 'txt'
}

function sanitizeDownloadName(language, index) {
  const ext = getCodeExtension(language)
  return `code-${index}.${ext}`
}

function downloadTextFile(text, filename, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([String(text ?? '')], { type: mime })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function downloadCodeBlock(source, language, index) {
  if (!canDownloadCode(language)) return
  const ext = getCodeExtension(language)
  const filename = sanitizeDownloadName(language, index)
  const mime = ext === 'json' ? 'application/json;charset=utf-8' : 'text/plain;charset=utf-8'
  downloadTextFile(source, filename, mime)
}

async function copyCodeBlock(source, button) {
  if (!source) return

  const setCopied = () => {
    if (!button) return
    button.classList.add('is-copied')
    button.setAttribute('aria-label', '已複製程式碼')
    button.setAttribute('title', '已複製')
    const label = button.querySelector('.code-action-label')
    if (label) label.textContent = '已複製'

    clearTimeout(Number(button.dataset.copyTimer || 0))
    button.dataset.copyTimer = String(
      window.setTimeout(() => {
        button.classList.remove('is-copied')
        button.setAttribute('aria-label', '複製程式碼')
        button.setAttribute('title', '複製程式碼')
        if (label) label.textContent = '複製'
        delete button.dataset.copyTimer
      }, 1600)
    )
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(source)
    } else {
      fallbackCopy(source)
    }
    setCopied()
  } catch {
    fallbackCopy(source)
    setCopied()
  }
}

function enhanceCode(container) {
  if (!container) return

  container.querySelectorAll('pre code').forEach((code, index) => {
    const pre = code.parentElement
    if (!pre || pre.dataset.enhanced) return

    pre.dataset.enhanced = '1'

    const languageClass = [...code.classList].find(x => x.startsWith('language-'))
    const language = languageClass ? languageClass.substring(9) : ''
    const normalizedLanguage = normalizeCodeLanguage(language)
    const source = code.textContent || ''

    try {
      if (language && syntaxHighlighter?.getLanguage(language)) {
        code.innerHTML = syntaxHighlighter.highlight(source, { language }).value
      } else {
        code.innerHTML = syntaxHighlighter?.highlightAuto(source).value || escapeHtml(source)
      }
    } catch {
      // ignore
    }

    code.classList.add('hljs')

    const toolbar = document.createElement('div')
    toolbar.className = 'code-toolbar'
    toolbar.dataset.hasDownload = canDownloadCode(normalizedLanguage) ? '1' : '0'

    const label = document.createElement('span')
    label.className = 'code-language'
    label.textContent = language || 'code'

    const actions = document.createElement('div')
    actions.className = 'code-actions'

    // 所有程式碼區塊都有複製；這就是 bash / txt / powershell 等非下載類型唯一的操作。
    const copyButton = document.createElement('button')
    copyButton.type = 'button'
    copyButton.className = 'code-action-button code-copy-button'
    copyButton.title = '複製程式碼'
    copyButton.setAttribute('aria-label', '複製程式碼')
    copyButton.innerHTML = '<span class="copy-icon" aria-hidden="true"></span><span class="code-action-label">複製</span>'
    copyButton.addEventListener('click', event => {
      event.preventDefault()
      event.stopPropagation()
      void copyCodeBlock(source, copyButton)
    })
    actions.appendChild(copyButton)

    if (canDownloadCode(normalizedLanguage)) {
      const downloadButton = document.createElement('button')
      downloadButton.type = 'button'
      downloadButton.className = 'code-action-button code-download-button'
      downloadButton.title = '下載程式碼'
      downloadButton.setAttribute('aria-label', '下載程式碼')
      downloadButton.innerHTML = '<span class="download-icon" aria-hidden="true"></span><span class="code-action-label">下載</span>'
      downloadButton.addEventListener('click', event => {
        event.preventDefault()
        event.stopPropagation()
        downloadCodeBlock(source, normalizedLanguage, index + 1)
      })
      actions.appendChild(downloadButton)
    }

    toolbar.appendChild(label)
    toolbar.appendChild(actions)
    pre.insertBefore(toolbar, code)
  })
}

function renderMath(container) {
  if (!container) return

  const walker =
    document.createTreeWalker(
      container,
      NodeFilter.SHOW_TEXT
    )

  const nodes = []

  let node

  while ((node = walker.nextNode())) {
    const parent = node.parentElement

    if (
      !parent ||
      ['CODE', 'PRE', 'SCRIPT', 'STYLE'].includes(
        parent.tagName
      )
    ) {
      continue
    }

    if (
      /\$\$[\s\S]+?\$\$|\$[^$]+\$/.test(
        node.nodeValue
      )
    ) {
      nodes.push(node)
    }
  }

  for (const textNode of nodes) {
    const text = textNode.nodeValue

    const regex =
      /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g

    let match

    let lastIndex = 0

    const fragment =
      document.createDocumentFragment()

    while ((match = regex.exec(text))) {
      if (match.index > lastIndex) {
        fragment.appendChild(
          document.createTextNode(
            text.slice(
              lastIndex,
              match.index
            )
          )
        )
      }

      const latex =
        match[1] ?? match[2]

      const display =
        Boolean(match[1])

      const element =
        document.createElement(
          display ? 'div' : 'span'
        )

      if (display) {
        element.className =
          'math-display'
      }

      try {
        katexRenderer.render(
          latex,
          element,
          {
            displayMode: display,
            throwOnError: false,
            strict: false,
            trust: false
          }
        )
      } catch {
        element.textContent =
          match[0]
      }

      fragment.appendChild(element)

      lastIndex = regex.lastIndex
    }

    if (lastIndex < text.length) {
      fragment.appendChild(
        document.createTextNode(
          text.slice(lastIndex)
        )
      )
    }

    textNode.parentNode?.replaceChild(
      fragment,
      textNode
    )
  }
}

async function refreshRenderedContent() {
  await ensureRichTextRenderer()
  await nextTick()

  document
    .querySelectorAll('.assistant-content')
    .forEach(element => {
      // Vue 的 v-html 會在切換對話或其他 reactive 更新時重新寫入 innerHTML。
      // 因此不能只靠根節點的 data-render-enhanced 判斷，否則裡面的
      // code toolbar 會被 Vue 清掉後就永遠不會再建立。
      enhanceCode(element)

      // KaTeX 仍維持一次性處理，避免重複把已渲染的公式再解析一次。
      if (element.dataset.mathEnhanced !== '1') {
        renderMath(element)
        element.dataset.mathEnhanced = '1'
      }
    })
}

function queueCodeRenderRefresh() {
  if (codeRenderRefreshQueued) return
  codeRenderRefreshQueued = true

  requestAnimationFrame(async () => {
    codeRenderRefreshQueued = false
    await refreshRenderedContent()
  })
}

function startCodeRenderObserver() {
  if (codeRenderObserver) return

  const target = document.getElementById('chatScroll')
  if (!target || typeof MutationObserver === 'undefined') return

  codeRenderObserver = new MutationObserver(mutations => {
    // 只有聊天內容真的變動才重新檢查，避免一般 UI 更新造成不必要工作。
    if (mutations.some(mutation => mutation.type === 'childList' || mutation.type === 'characterData')) {
      queueCodeRenderRefresh()
    }
  })

  codeRenderObserver.observe(target, {
    childList: true,
    subtree: true
  })

  queueCodeRenderRefresh()
}

function stopCodeRenderObserver() {
  codeRenderObserver?.disconnect()
  codeRenderObserver = null
  codeRenderRefreshQueued = false
}

async function checkSession() {
  try {
    const data = await api('/check_session')

    authView.value = !data.logged_in

    if (data.logged_in) {
      // Google 登入優先使用 Google profile name，避免把 google_xxxxx / Google sub 顯示給使用者。
      setAccountDisplayName(
        data.display_name ||
        data.name ||
        data.username ||
        data.account ||
        data.user?.display_name ||
        data.user?.username ||
        data.user?.account ||
        localStorage.getItem('ggpt_account_name') ||
        'GGPT 使用者'
      )

      // 不要讓聊天室/Token API 阻塞首屏顯示；兩者也可以同時載入。
      void Promise.allSettled([
        loadConversations()
      ])
    } else {
      // 只有登入畫面才載入 Google SDK，已登入的首屏完全跳過第三方 SDK。
      loadGoogleSdk()
    }
  } catch (error) {
    authView.value = true
    console.error(error)
  }
}

async function login() {
  if (!username.value || !password.value) {
    errorMessage.value =
      '請輸入帳號與密碼'
    return
  }

  loading.value = true
  errorMessage.value = ''

  try {
    const loginData = await api('/login', {
      method: 'POST',
      headers: {
        'Content-Type':
          'application/json'
      },
      body: JSON.stringify({
        username: username.value,
        password: password.value
      })
    })

    setAccountDisplayName(
      loginData?.display_name ||
      loginData?.name ||
      loginData?.username ||
      loginData?.account ||
      loginData?.user?.display_name ||
      loginData?.user?.username ||
      loginData?.user?.account ||
      username.value
    )
    authView.value = false

    void Promise.allSettled([
      loadConversations()
    ])

    await nextTick()
    bindScrollObserver()

    username.value = ''
    password.value = ''
  } catch (error) {
    errorMessage.value =
      error.message
  } finally {
    loading.value = false
  }
}

async function register() {
  if (!username.value || !password.value) {
    errorMessage.value =
      '請輸入帳號與密碼'
    return
  }

  const validationError = registrationValidationError()
  if (validationError) {
    errorMessage.value = validationError
    return
  }

  loading.value = true
  errorMessage.value = ''

  try {
    await api('/register', {
      method: 'POST',
      headers: {
        'Content-Type':
          'application/json'
      },
      body: JSON.stringify({
        username: username.value,
        password: password.value
      })
    })

    errorMessage.value =
      '註冊成功，請登入'
  } catch (error) {
    errorMessage.value =
      error.message
  } finally {
    loading.value = false
  }
}

async function logout() {
  try {
    await api('/logout', { method: 'POST' })
  } catch {
    // ignore
  }

  localStorage.removeItem('ggpt_account_name')
  location.reload()
}

async function loadConversations() {
  let joinedConversationId = null

  if (pendingShareToken) {
    try {
      const joined = await api(
        `/shared-conversations/${encodeURIComponent(pendingShareToken)}/join`,
        { method: 'POST' }
      )
      joinedConversationId = Number(joined?.conversation?.id) || null
      linkedSharedConversationId = joinedConversationId
    } catch (error) {
      errorMessage.value = error.message
    }
  }

  const data =
    await api('/conversations')

  conversations.value =
    Array.isArray(data) ? data : []

  if (!conversations.value.length) {
    const created =
      await api('/conversations', {
        method: 'POST',
        headers: {
          'Content-Type':
            'application/json'
        },
        body: JSON.stringify({
          title: '新對話'
        })
      })

    conversations.value =
      [created]
  }

  const saved =
    Number(
      localStorage.getItem(
        'ggpt_current_conversation'
      )
    )

  const found =
    conversations.value.find(
      x => x.id === (joinedConversationId || saved)
    )

  await selectConversation(
    found
      ? found.id
      : conversations.value[0].id
  )
}

function mapConversationHistory(history) {
  const nextMessages = []

  for (const item of history) {
    nextMessages.push({
      role: 'user',
      authorName: item.author_name || '使用者',
      isMine: Boolean(item.is_mine),
      text: item.user_message || '',
      imageUrl: item.image_url || null,
      attachments: Array.isArray(item.attachments) ? item.attachments : [],
      createdAt: item.created_at || item.timestamp || null
    })

    nextMessages.push({
      role: 'assistant',
      text: item.bot_reply || '',
      model: item.model || null,
      reasoningSummary: item.reasoning_summary || '',
      reasoningExpanded: false,
      webSources: [],
      createdAt: item.created_at || item.timestamp || null
    })
  }

  return nextMessages
}

async function selectConversation(id) {
  currentConversationId.value =
    Number(id)

  const targetPath = (
    pendingShareToken && Number(id) === linkedSharedConversationId
  )
    ? `/c/${pendingShareToken}`
    : '/'
  if (router.currentRoute.value.path !== targetPath) {
    await router.replace(targetPath)
  }

  localStorage.setItem(
    'ggpt_current_conversation',
    String(id)
  )

  sidebarOpen.value = false

  try {
    const history =
      await api(
        `/conversations/${id}/messages`
      )

    messages.value = mapConversationHistory(history)
    lastConversationRecordId = history.length
      ? history[history.length - 1].id
      : null
    visibleMessageCount.value = MESSAGE_BATCH_SIZE

    await scrollBottom()
    await refreshRenderedContent()
  } catch (error) {
    console.error(error)
  }
}

async function createConversation() {
  try {
    const conversation =
      await api('/conversations', {
        method: 'POST',
        headers: {
          'Content-Type':
            'application/json'
        },
        body: JSON.stringify({
          title: '新對話'
        })
      })

    conversations.value = [conversation, ...conversations.value]

    await selectConversation(
      conversation.id
    )
  } catch (error) {
    errorMessage.value =
      error.message
  }
}

async function copyShareLink() {
  if (!shareLink.value) return

  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error('Clipboard API unavailable')
    }
    await navigator.clipboard.writeText(shareLink.value)
  } catch {
    fallbackCopy(shareLink.value)
  }

  shareCopied.value = true
  window.setTimeout(() => {
    shareCopied.value = false
  }, 1800)
}

async function shareConversation() {
  const conversation = currentConversation.value
  if (!conversation || !conversation.is_owner || shareLoading.value) return

  shareLoading.value = true
  shareError.value = ''

  try {
    const data = await api(
      `/conversations/${conversation.id}/share`,
      { method: 'POST' }
    )
    shareLink.value = `${window.location.origin}${data.share_path}`
    shareModalOpen.value = true
    conversations.value = conversations.value.map(item =>
      item.id === conversation.id
        ? { ...item, share_enabled: 1 }
        : item
    )
    await copyShareLink()
  } catch (error) {
    shareError.value = error.message
    shareModalOpen.value = true
  } finally {
    shareLoading.value = false
  }
}

async function revokeConversationShare() {
  const conversation = currentConversation.value
  if (!conversation?.is_owner || shareLoading.value) return

  if (!window.confirm('停止分享後，舊連結與目前協作者都會失去這個對話的權限。確定繼續？')) {
    return
  }

  shareLoading.value = true
  shareError.value = ''

  try {
    await api(
      `/conversations/${conversation.id}/share`,
      { method: 'DELETE' }
    )
    conversations.value = conversations.value.map(item =>
      item.id === conversation.id
        ? { ...item, share_enabled: 0 }
        : item
    )
    shareModalOpen.value = false
    shareLink.value = ''
  } catch (error) {
    shareError.value = error.message
  } finally {
    shareLoading.value = false
  }
}

async function renameConversation() {
  if (!currentConversationId.value) return

  const conversation =
    currentConversation.value

  if (!conversation) return

  const title =
    window.prompt(
      '輸入新的對話名稱',
      conversation.title || '新對話'
    )

  if (!title?.trim()) return

  try {
    const updated =
      await api(
        `/conversations/${conversation.id}`,
        {
          method: 'PATCH',
          headers: {
            'Content-Type':
              'application/json'
          },
          body: JSON.stringify({
            title:
              title.trim()
          })
        }
      )

    conversations.value = conversations.value.map(item =>
      item.id === conversation.id
        ? { ...item, title: updated.title }
        : item
    )
  } catch (error) {
    errorMessage.value =
      error.message
  }
}

async function deleteConversation(id) {
  const conversation =
    conversations.value.find(
      x => x.id === id
    )

  if (!conversation) return

  if (
    !window.confirm(
      conversation.is_owner
        ? `確定刪除「${conversation.title}」？共享成員也會失去這個對話。`
        : `確定離開共享對話「${conversation.title}」？`
    )
  ) {
    return
  }

  try {
    await api(
      `/conversations/${id}`,
      {
        method: 'DELETE'
      }
    )

    conversations.value =
      conversations.value.filter(
        x => x.id !== id
      )

    if (
      currentConversationId.value === id
    ) {
      if (!conversations.value.length) {
        await createConversation()
      } else {
        await selectConversation(
          conversations.value[0].id
        )
      }
    }
  } catch (error) {
    errorMessage.value =
      error.message
  }
}

async function fileToDataUrl(file) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('讀取檔案失敗'))
    reader.readAsDataURL(file)
  })
}

async function sendMessage() {
  if (
    waiting.value ||
    !currentConversationId.value
  ) {
    return
  }

  const text = messageInput.value.trim()

  if (
    !text &&
    !pendingImage.value &&
    !pendingFiles.value.length
  ) {
    return
  }

  waiting.value = true
  thinkingLabel.value = reasoningEnabled.value && modelSupportsReasoning()
    ? '正在思考…'
    : '正在整理回覆…'

  try {
    // 附件選擇階段完全不碰後端；只有真正送出聊天時才準備必要資料。
    const pending = pendingFiles.value.slice()
    const imageItem = pending.find(item => item.kind === 'image') || null
    const image = imageItem?.preview_url || null

    let imageDataUrl = null
    const attachments = []
    for (const file of pending) {
      const base = {
        id: file.id,
        name: file.name,
        mime_type: file.mime_type || file.type || '',
        size: Number(file.size || 0),
        content: file.content || null
      }

      if (file.kind === 'image' && file.file) {
        // 圖片走獨立 image_data_url 欄位，避免同一張圖在 attachments 中送兩次。
        imageDataUrl = await fileToDataUrl(file.file)
      }

      // 純文字檔直接送內容即可，不需要先上傳檔案。
      attachments.push(base)
    }

    const displayImage = image || (imageItem?.data_url || null)

    messageInput.value = ''
    await nextTick()
    resizeComposer(composerTextarea.value)
    pendingImage.value = null
    pendingFiles.value = []

    messages.value = [...messages.value, {
      role: 'user',
      authorName: accountName.value || '你',
      isMine: true,
      text,
      imageUrl: displayImage,
      attachments: attachments.map(item => ({
        ...item,
        // 聊天畫面立即使用本地物件 URL，不等後端。
        url: pending.find(x => x.id === item.id)?.preview_url || null
      })),
      createdAt: new Date().toISOString()
    }]

    const data =
      await api('/chat', {
        method: 'POST',
        headers: {
          'Content-Type':
            'application/json'
        },
        body: JSON.stringify({
          conversation_id:
            currentConversationId.value,
          message: text,
          image_url: null,
          image_data_url: imageDataUrl,
          attachments,
          model: model.value,
          web_search:
            webSearchEnabled.value,
          reasoning_enabled:
            modelSupportsReasoning() && reasoningEnabled.value,
          reasoning_effort:
            modelSupportsReasoning() && reasoningEnabled.value
              ? reasoningEffort.value
              : null
        })
      })

    messages.value = [...messages.value, {
      role: 'assistant',
      text:
        data.reply ||
        data.error ||
        '沒有收到回覆。',
      model:
        data.model || model.value,
      reasoningSummary:
        data.reasoning_summary || '',
      reasoningExpanded: false,
      webSources:
        data.web_sources || [],
      createdAt: new Date().toISOString()
    }]

    if (data.conversation) {
      const index =
        conversations.value.findIndex(
          x =>
            x.id ===
            data.conversation.id
        )

      if (index >= 0) {
        conversations.value = conversations.value.map((item, itemIndex) =>
          itemIndex === index ? { ...item, ...data.conversation } : item
        )
      }
    }

    await scrollBottom()
    await refreshRenderedContent()
  } catch (error) {
    messages.value = [...messages.value, {
      role: 'assistant',
      text:
        `抱歉，發生錯誤：\n\n${error.message}`,
      model: model.value,
      webSources: [],
      createdAt: new Date().toISOString()
    }]
  } finally {
    waiting.value = false
  }
}

function isImageFile(file) {
  if (!file) return false
  const mime = String(file.type || '').toLowerCase()
  if (mime.startsWith('image/')) return true
  return /\.(png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif)$/i.test(file.name || '')
}

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

const browserReadableSpecialNames = new Set([
  'dockerfile', 'containerfile', 'makefile', 'license', 'licence',
  'readme', 'readme.md', 'robots.txt', '.env', '.gitignore',
  '.gitattributes', '.editorconfig', '.npmrc', '.yarnrc',
  'requirements.txt', 'pipfile', 'gemfile', 'rakefile'
])

const browserReadableExt =
  /\.(txt|text|md|markdown|rst|csv|tsv|json|jsonl|xml|rss|atom|html?|xhtml|css|scss|sass|less|styl|vue|svelte|astro|js|mjs|cjs|jsx|ts|mts|cts|tsx|py|pyw|pyi|java|kt|kts|scala|groovy|c|cc|cpp|cxx|h|hh|hpp|hxx|cs|csx|fs|fsx|vb|go|rs|swift|m|mm|dart|lua|r|pl|pm|rb|php|zig|nim|d|ex|exs|erl|hrl|sh|bash|zsh|fish|ksh|csh|bat|cmd|ps1|psm1|psd1|vbs|vbe|wsf|sql|ddl|dml|prisma|graphql|gql|proto|cmake|make|mk|gradle|sbt|toml|ini|cfg|conf|config|properties|env|yaml|yml|tex|bib|log|ino|asm|s|inc)$/i

async function readBrowserTextFile(file) {
  if (!file) return null

  const name = String(file.name || '').toLowerCase()
  const type = String(file.type || '').toLowerCase()

  if (
    !type.startsWith('text/') &&
    !browserReadableExt.test(name) &&
    !browserReadableSpecialNames.has(name)
  ) {
    return null
  }

  try {
    let content = await file.text()
    if (content.length > 180000) {
      content =
        content.slice(0, 180000) +
        '\n\n[檔案內容已截斷，原始檔案仍會一併上傳。]'
    }
    return content
  } catch {
    return null
  }
}

async function uploadOneFile(file) {
  if (!file) return false

  if (file.size > MAX_UPLOAD_BYTES) {
    errorMessage.value = '單一檔案請小於 50MB'
    return false
  }

  uploading.value = true

  try {
    const image = isImageFile(file)
    const content = image ? null : await readBrowserTextFile(file)
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const previewUrl = URL.createObjectURL(file)
    localObjectUrls.add(previewUrl)

    const item = {
      id,
      kind: image ? 'image' : 'file',
      name: file.name || '附件',
      preview_url: previewUrl,
      mime_type: file.type || 'application/octet-stream',
      type: file.type || '',
      size: Number(file.size || 0),
      content,
      file
    }

    if (image) {
      pendingImage.value = previewUrl
      pendingFiles.value = [
        ...pendingFiles.value.filter(existing => existing.kind !== 'image'),
        item
      ]
    } else {
      pendingFiles.value = [...pendingFiles.value, item]
    }

    errorMessage.value = ''
    return true
  } catch (error) {
    errorMessage.value = error?.message || '讀取檔案失敗'
    console.error('GGPT local attachment failed:', error)
    return false
  } finally {
    uploading.value = false
  }
}

async function handleUploadInput(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''

  for (const file of files) {
    await uploadOneFile(file)
  }
}

async function handleDrop(event) {
  draggingFiles.value = false

  const files = Array.from(
    event.dataTransfer?.files || []
  )

  if (!files.length) return

  for (const file of files) {
    await uploadOneFile(file)
  }
}

async function handlePaste(event) {
  const clipboard = event.clipboardData
  if (!clipboard) return

  const files = []
  const seen = new Set()

  // files 先取，部分瀏覽器會直接提供檔案。
  for (const file of Array.from(clipboard.files || [])) {
    const key = `${file.name}:${file.size}:${file.lastModified}`
    if (!seen.has(key)) {
      seen.add(key)
      files.push(file)
    }
  }

  // 再掃描 DataTransferItem，補上剪貼簿圖片與部分特殊檔案。
  for (const item of Array.from(clipboard.items || [])) {
    if (item.kind !== 'file') continue
    const file = item.getAsFile?.()
    if (!file) continue

    const fallbackName =
      file.name ||
      (
        String(file.type || '').startsWith('image/')
          ? `pasted-image-${Date.now()}.${String(file.type).split('/')[1] || 'png'}`
          : `pasted-file-${Date.now()}`
      )

    const normalizedFile =
      file.name
        ? file
        : new File(
            [file],
            fallbackName,
            {
              type: file.type || 'application/octet-stream',
              lastModified: Date.now()
            }
          )

    const key = `${normalizedFile.name}:${normalizedFile.size}:${normalizedFile.lastModified}`
    if (!seen.has(key)) {
      seen.add(key)
      files.push(normalizedFile)
    }
  }

  if (!files.length) return

  // 有檔案時才阻止一般文字貼上，避免 Ctrl+V 文字行為被破壞。
  event.preventDefault()

  for (const file of files) {
    await uploadOneFile(file)
  }
}

function releaseLocalFile(item) {
  if (item?.preview_url) {
    URL.revokeObjectURL(item.preview_url)
    localObjectUrls.delete(item.preview_url)
  }
}

function removePendingFile(id) {
  const removed = pendingFiles.value.find(item => item.id === id)
  releaseLocalFile(removed)

  pendingFiles.value =
    pendingFiles.value.filter(
      item => item.id !== id
    )

  if (removed?.kind === 'image') {
    pendingImage.value = null
  }
}

function clearPendingImage() {
  const image = pendingFiles.value.find(item => item.kind === 'image')
  releaseLocalFile(image)
  pendingImage.value = null
  pendingFiles.value =
    pendingFiles.value.filter(
      item => item.kind !== 'image'
    )
}

function formatFileSize(bytes) {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

/* GLOBAL CHAT */
async function loadGlobalChat() {
  if (authView.value) return

  try {
    const data =
      await api('/api/global_chat/messages')

    globalChatMessages.value =
      Array.isArray(data)
        ? data
        : (
            data.messages ||
            data.items ||
            []
          )

    globalChatError.value = ''
  } catch (error) {
    globalChatError.value =
      error.message
    console.error(
      'Global Chat 讀取失敗',
      error
    )
  }
}

async function sendGlobalChat() {
  const text =
    globalChatInput.value.trim()

  if (
    !text ||
    globalChatSending.value
  ) {
    return
  }

  globalChatSending.value = true

  try {
    const data =
      await api(
        '/api/global_chat/messages',
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json'
          },
          body: JSON.stringify({
            message: text
          })
        }
      )

    globalChatInput.value = ''

    if (data?.message) {
      globalChatMessages.value = [
        ...globalChatMessages.value,
        data.message
      ]
    } else {
      await loadGlobalChat()
    }
  } catch (error) {
    globalChatError.value =
      error.message
  } finally {
    globalChatSending.value = false
  }
}

function openAttachment(url) {
  if (!url) return
  window.open(
    url,
    '_blank',
    'noopener,noreferrer'
  )
}

function formatGlobalChatTime(value) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleTimeString(
      'zh-TW',
      {
        hour: '2-digit',
        minute: '2-digit'
      }
    )
  } catch {
    return ''
  }
}

function startGlobalChat() {
  if (authView.value || globalChatTimer || !globalChatOpen.value) return

  void loadGlobalChat()

  globalChatTimer =
    window.setInterval(
      () => {
        if (
          globalChatOpen.value &&
          document.visibilityState === 'visible'
        ) {
          void loadGlobalChat()
        }
      },
      5000
    )
}

function stopGlobalChat() {
  if (globalChatTimer) {
    clearInterval(globalChatTimer)
    globalChatTimer = null
  }
}

function toggleGlobalChat(force) {
  globalChatOpen.value = typeof force === 'boolean'
    ? force
    : !globalChatOpen.value

  if (globalChatOpen.value) {
    startGlobalChat()
  } else {
    stopGlobalChat()
  }
}
async function refreshUsage() {
  try {
    usage.value =
      await api('/usage')
  } catch (error) {
    console.error(
      'Token 統計讀取失敗',
      error
    )
  }
}

async function initGoogle() {
  try {
    const config =
      await api(
        '/auth/google/config'
      )

    googleEnabled.value =
      Boolean(
        config.enabled &&
        config.client_id
      )

    googleClientId.value =
      config.client_id || ''

    if (
      googleEnabled.value &&
      window.google
    ) {
      window.google.accounts.id.initialize({
        client_id:
          googleClientId.value,
        callback:
          handleGoogleCredential
      })

      googleReady.value = true

      await nextTick()

      const target =
        document.getElementById(
          'googleSignIn'
        )

      if (target) {
        window.google.accounts.id.renderButton(
          target,
          {
            theme: 'outline',
            size: 'large',
            width: 330
          }
        )
      }
    }
  } catch (error) {
    console.error(
      'Google Login 初始化失敗',
      error
    )
  }
}

async function handleGoogleCredential(response) {
  if (!response?.credential) {
    return
  }

  try {
    const googleData = await api(
      '/auth/google',
      {
        method: 'POST',
        headers: {
          'Content-Type':
            'application/json'
        },
        body: JSON.stringify({
          credential:
            response.credential
        })
      }
    )

    setAccountDisplayName(
      googleData?.display_name ||
      googleData?.name ||
      googleData?.username ||
      googleData?.account ||
      googleData?.user?.display_name ||
      googleData?.user?.username ||
      accountName.value ||
      'Google 使用者'
    )
    authView.value = false

    void Promise.allSettled([
      loadConversations()
    ])

    await nextTick()
    bindScrollObserver()
  } catch (error) {
    errorMessage.value =
      error.message
  }
}


const conversationSearchInput = ref('')
const conversationSearch = ref('')
const showScrollToBottom = ref(false)
const lightTheme = ref(localStorage.getItem('ggpt_theme') === 'light')
const CONVERSATION_BATCH_SIZE = 50
const MESSAGE_BATCH_SIZE = 80
const visibleConversationCount = ref(CONVERSATION_BATCH_SIZE)
const visibleMessageCount = ref(MESSAGE_BATCH_SIZE)
let conversationSearchTimer = null
let composerResizeFrame = 0
let chatScrollFrame = 0
let chatScrollElement = null

const filteredConversations = computed(() => {
  const q = conversationSearch.value.trim().toLowerCase()
  if (!q) return conversations.value
  return conversations.value.filter(item =>
    String(item.title || '新對話').toLowerCase().includes(q)
  )
})

const visibleConversations = computed(() =>
  filteredConversations.value.slice(0, visibleConversationCount.value)
)

const visibleMessages = computed(() =>
  messages.value.slice(-visibleMessageCount.value)
)

const pendingNonImageFiles = computed(() =>
  pendingFiles.value.filter(item => item.kind !== 'image')
)

function handleConversationSearch(event) {
  const value = event.target.value
  clearTimeout(conversationSearchTimer)
  conversationSearchTimer = setTimeout(() => {
    conversationSearch.value = value
    visibleConversationCount.value = CONVERSATION_BATCH_SIZE
  }, 250)
}

function clearConversationSearch() {
  clearTimeout(conversationSearchTimer)
  conversationSearchInput.value = ''
  conversationSearch.value = ''
  visibleConversationCount.value = CONVERSATION_BATCH_SIZE
}

function showMoreConversations() {
  visibleConversationCount.value += CONVERSATION_BATCH_SIZE
}

function showEarlierMessages() {
  visibleMessageCount.value += MESSAGE_BATCH_SIZE
}

function toggleTheme() {
  lightTheme.value = !lightTheme.value
  localStorage.setItem('ggpt_theme', lightTheme.value ? 'light' : 'dark')
}

function copyMessage(text) {
  if (!text) return
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text)
      .then(() => {
        errorMessage.value = ''
      })
      .catch(() => fallbackCopy(text))
  } else {
    fallbackCopy(text)
  }
}

function fallbackCopy(text) {
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try { document.execCommand('copy') } catch {}
  area.remove()
}

function resizeComposer(target) {
  if (!target) return
  target.style.height = 'auto'
  target.style.height = `${Math.min(target.scrollHeight, 190)}px`
}

function scheduleComposerResize(target) {
  cancelAnimationFrame(composerResizeFrame)
  composerResizeFrame = requestAnimationFrame(() => resizeComposer(target))
}

function scrollToBottomSmooth() {
  const container = document.getElementById('chatScroll')
  if (container) {
    container.scrollTo({
      top: container.scrollHeight,
      behavior: 'smooth'
    })
  }
}

function formatMessageTime(value) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleTimeString('zh-TW', {
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return ''
  }
}

function scrollBottom() {
  return nextTick(() => {
    const container =
      document.getElementById(
        'chatScroll'
      )

    if (container) {
      container.scrollTop =
        container.scrollHeight
    }
  })
}

async function refreshSharedConversation() {
  const conversation = currentConversation.value
  if (
    authView.value ||
    waiting.value ||
    document.visibilityState !== 'visible' ||
    !conversation ||
    (!conversation.is_shared && !conversation.share_enabled)
  ) {
    return
  }

  try {
    const history = await api(`/conversations/${conversation.id}/messages`)
    const latestId = history.length ? history[history.length - 1].id : null
    if (latestId === lastConversationRecordId) return

    const container = document.getElementById('chatScroll')
    const wasNearBottom = !container || (
      container.scrollHeight - container.scrollTop - container.clientHeight < 300
    )

    messages.value = mapConversationHistory(history)
    lastConversationRecordId = latestId
    await refreshRenderedContent()
    if (wasNearBottom) await scrollBottom()
  } catch (error) {
    if (error?.status === 403 || error?.status === 404) {
      errorMessage.value = '這個共同對話已停止分享。'
      await loadConversations()
      return
    }
    console.warn('共同對話同步失敗：', error)
  }
}

function startSharedConversationSync() {
  if (sharedConversationTimer) return
  sharedConversationTimer = window.setInterval(
    () => void refreshSharedConversation(),
    4000
  )
}

function stopSharedConversationSync() {
  if (!sharedConversationTimer) return
  clearInterval(sharedConversationTimer)
  sharedConversationTimer = null
}

function sendSuggestion(text) {
  messageInput.value = text
}

function handleKeydown(event) {
  if (
    event.key === 'Enter' &&
    !event.shiftKey &&
    !event.isComposing
  ) {
    event.preventDefault()
    sendMessage()
  }
}


function updateScrollButton(container) {
  const distance = container.scrollHeight - container.scrollTop - container.clientHeight
  showScrollToBottom.value = distance > 260
}

function handleChatScroll() {
  if (chatScrollFrame || !chatScrollElement) return
  chatScrollFrame = requestAnimationFrame(() => {
    chatScrollFrame = 0
    if (chatScrollElement) updateScrollButton(chatScrollElement)
  })
}

function bindScrollObserver() {
  const container = document.getElementById('chatScroll')
  if (!container || container === chatScrollElement) return
  chatScrollElement?.removeEventListener('scroll', handleChatScroll)
  chatScrollElement = container
  chatScrollElement.addEventListener('scroll', handleChatScroll, { passive: true })
}

function loadGoogleSdk() {
  if (window.google?.accounts?.id) {
    void initGoogle()
    return
  }

  if (document.querySelector('script[data-ggpt-google-sdk]')) {
    return
  }

  const script = document.createElement('script')
  script.src = 'https://accounts.google.com/gsi/client'
  script.async = true
  script.defer = true
  script.dataset.ggptGoogleSdk = '1'
  script.onload = () => {
    void initGoogle()
  }
  script.onerror = () => {
    console.warn('Google Login SDK 載入失敗')
  }
  document.head.appendChild(script)
}

onMounted(async () => {
  // 先完成 session 判斷；已登入使用者不再下載 Google SDK，避免浪費首屏網路與初始化時間。
  await checkSession()
  await nextTick()
  bindScrollObserver()
  startCodeRenderObserver()
  startSharedConversationSync()

  // 讓 Ctrl/Cmd + V 在聊天輸入區直接貼上圖片或檔案。
  // 監聽 document 是為了兼容 textarea 外層被焦點管理元件攔截的情況，
  // 實際只有 clipboard 中存在檔案時才會 preventDefault。
  document.addEventListener('paste', handlePaste)
})

onUnmounted(() => {
  document.removeEventListener('paste', handlePaste)
  chatScrollElement?.removeEventListener('scroll', handleChatScroll)
  chatScrollElement = null
  clearTimeout(conversationSearchTimer)
  cancelAnimationFrame(composerResizeFrame)
  cancelAnimationFrame(chatScrollFrame)
  stopCodeRenderObserver()
  stopGlobalChat()
  stopSharedConversationSync()
  for (const url of localObjectUrls) {
    URL.revokeObjectURL(url)
  }
  localObjectUrls.clear()
})
</script>

<template>
  <div v-if="authView" class="auth-page">
    <div class="auth-orb orb-a"></div>
    <div class="auth-orb orb-b"></div>

    <div class="auth-card">
      <div class="auth-brand">
        <div class="auth-logo">✦</div>
        <div>
          <strong>GGPT</strong>
          <small>AI Workspace</small>
        </div>
      </div>

      <div class="auth-copy">
        <div class="eyebrow">YOUR AI WORKSPACE</div>
        <h1>登入GGPT</h1>
        <p v-if="pendingShareToken" class="share-invite-notice">
          你正透過分享連結加入共同對話，登入後會自動開啟。
        </p>
      </div>

      <form class="auth-form" @submit.prevent="login">
        <label for="username">帳號</label>
        <div class="auth-field">
          <span>＠</span>
          <input
            id="username"
            v-model="username"
            placeholder="輸入帳號"
            autocomplete="username"
            minlength="3"
            maxlength="32"
            autocapitalize="none"
            spellcheck="false"
            required
          />
        </div>

        <label for="password">密碼</label>
        <div class="auth-field">
          <span>••</span>
          <input
            id="password"
            v-model="password"
            type="password"
            placeholder="輸入密碼"
            autocomplete="current-password"
            minlength="8"
            maxlength="128"
            required
          />
        </div>

        <button class="primary auth-submit" :disabled="loading">
          <span v-if="loading" class="spinner"></span>
          {{ loading ? '登入中…' : '登入 GGPT' }}
        </button>

        <button
          type="button"
          class="secondary auth-register"
          :disabled="loading"
          @click="register"
        >
          建立新帳號
        </button>

        <div v-if="errorMessage" class="error-message">
          {{ errorMessage }}
        </div>
      </form>

      <div class="auth-divider"><span></span><em>或使用</em><span></span></div>

      <div id="googleSignIn" class="google-login"></div>

      <div class="auth-footer">
        <span class="status-dot"></span>
      <!-- Secure session · Google Login 可選-->
      </div>
    </div>
  </div>

  <div v-else class="app" :class="{ light: lightTheme }">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <div class="sidebar-top">
        <div class="sidebar-brand">
          <div class="brand-logo">✦</div>
          <div>
            <strong>GGPT</strong>
            <small>對話工作區</small>
          </div>
        </div>

        <button class="new-chat" @click="createConversation">
          <span>＋</span>
          <strong>新對話</strong>
          <kbd>Ctrl K</kbd>
        </button>

        <div class="conversation-search">
          <span>⌕</span>
          <input
            v-model="conversationSearchInput"
            placeholder="搜尋對話…"
            @input="handleConversationSearch"
          />
          <button v-if="conversationSearchInput" @click="clearConversationSearch">×</button>
        </div>
      </div>

      <div class="history-head">
        <span>最近對話</span>
        <small>{{ conversations.length }}</small>
      </div>

      <div class="conversation-list">
        <div
          v-for="conversation in visibleConversations"
          :key="conversation.id"
          class="conversation-row"
          :class="{ active: conversation.id === currentConversationId }"
        >
          <button class="conversation-button" @click="selectConversation(conversation.id)">
            <span class="conversation-icon">{{ conversation.is_shared ? '♧' : '◌' }}</span>
            <span class="conversation-title">{{ conversation.title || '新對話' }}</span>
          </button>

          <button
            class="conversation-menu"
            @click.stop="deleteConversation(conversation.id)"
            :title="conversation.is_owner ? '刪除對話' : '離開共享對話'"
          >
            ×
          </button>
        </div>

        <div v-if="!filteredConversations.length" class="sidebar-empty">
          沒有符合的對話
        </div>

        <button
          v-if="visibleConversations.length < filteredConversations.length"
          class="history-load-more"
          @click="showMoreConversations"
        >
          顯示更多對話
        </button>
      </div>

      <div v-if="usage" class="usage-card" @click="usageModal = true; refreshUsage()">
        <div class="usage-top">
          <div>
            <span>Token 使用量</span>
            <strong>{{ Number(totalUsage.total_tokens || 0).toLocaleString() }}</strong>
          </div>
          <span class="usage-link">明細 →</span>
        </div>
        <div class="usage-bar">
          <span :style="{ width: `${Math.min(usage.percent || 0, 100)}%` }"></span>
        </div>
        <div class="usage-bottom">
          <span>{{ Number(totalUsage.requests || 0).toLocaleString() }} 次請求</span>
          <span>總計 ${{ Number(totalUsage.total_cost || 0).toFixed(4) }}</span>
        </div>
        <div class="usage-search-line">
          <span>⌁ Web Search</span>
          <span>{{ webSearchUsage.calls.toLocaleString() }} 次 · ${{ webSearchUsage.cost.toFixed(4) }}</span>
        </div>
      </div>

      <div class="sidebar-bottom">
        <button class="side-tool" @click="toggleTheme">
          <span>{{ lightTheme ? '☾' : '☀' }}</span>
          <span>{{ lightTheme ? '深色模式' : '明亮模式' }}</span>
        </button>

        <div class="account">
          <div class="account-avatar">{{ userInitial }}</div>
          <div class="account-info">
            <strong>{{ accountName || 'GGPT 使用者' }}</strong>
            <small>已登入</small>
          </div>
          <button class="logout" @click="logout" title="登出">↪</button>
        </div>
      </div>
    </aside>

    <div v-if="sidebarOpen" class="sidebar-backdrop" @click="sidebarOpen = false"></div>

    <main class="main">
      <header class="topbar">
        <button
          class="mobile-button"
          @click="sidebarOpen = !sidebarOpen"
          aria-label="開啟側邊欄"
        >☰</button>

        <div class="chat-title">
          <strong>{{ currentConversation?.title || '新對話' }}</strong>
          <span>{{ currentConversation?.is_shared ? '共同對話' : model }}</span>
        </div>

        <div class="top-actions">
          <button
            v-if="currentConversation?.is_owner"
            class="icon-button share-button"
            title="分享共同對話"
            :disabled="shareLoading"
            @click="shareConversation"
          >{{ shareLoading ? '…' : '↗' }}</button>
          <button
            v-if="currentConversation?.is_owner"
            class="icon-button"
            title="重新命名"
            @click="renameConversation"
          >✎</button>
          <button class="icon-button" title="Token 使用量" @click="usageModal = true; refreshUsage()">◫</button>
        </div>
      </header>

      <div id="chatScroll" class="chat-scroll">
        <div class="chat-box">
          <div v-if="messages.length === 0" class="welcome">
            <div class="welcome-mark">✦</div>
            <div class="eyebrow">GGPT 對話工作區</div>
            <h1>今天想一起完成什麼？</h1>
            <p>
              從問題解答到程式碼、數學、圖片分析與即時資訊搜尋，
              直接在同一個對話裡完成。
            </p>

            <div class="welcome-features" aria-label="可用功能">
              <span><i></i>即時搜尋</span>
              <span><i></i>檔案與圖片</span>
              <span><i></i>共同對話</span>
            </div>

            <div class="suggestions">
              <button @click="sendSuggestion('幫我分析這段 Python 程式碼，並找出可以改善的地方')">
                <span class="suggestion-icon">⌘</span>
                <span><strong>分析程式碼</strong><small>找 bug、重構與效能問題</small></span>
              </button>
              <button @click="sendSuggestion('幫我解一道數學題，步驟詳細並用 LaTeX 顯示公式')">
                <span class="suggestion-icon">√</span>
                <span><strong>解數學題</strong><small>逐步推導與公式整理</small></span>
              </button>
              <button @click="sendSuggestion('幫我規劃一個 Vue 3 網站架構，包含前後端 API 設計')">
                <span class="suggestion-icon">◫</span>
                <span><strong>規劃網站</strong><small>架構、元件與 API 一起想</small></span>
              </button>
              <button @click="sendSuggestion('請幫我整理最新資訊，並在需要時使用 Web Search')">
                <span class="suggestion-icon">⌁</span>
                <span><strong>搜尋最新資訊</strong><small>需要時開啟 Web Search</small></span>
              </button>
            </div>
          </div>

          <button
            v-if="visibleMessages.length < messages.length"
            class="history-load-more message-load-more"
            @click="showEarlierMessages"
          >
            顯示較早訊息（尚有 {{ messages.length - visibleMessages.length }} 則）
          </button>

          <div
            v-for="(message, index) in visibleMessages"
            :key="`${currentConversationId}-${index}`"
            class="message"
            :class="message.role"
          >
            <div class="avatar" :class="message.role">
              {{ message.role === 'user' ? 'U' : '✦' }}
            </div>

            <div class="message-main">
              <div class="message-head">
                <div class="message-author">
                  <strong>
                    {{ message.role === 'user' ? (message.isMine ? '你' : message.authorName || '參與者') : 'GGPT' }}
                  </strong>
                  <span v-if="message.model" class="model-badge">{{ message.model }}</span>
                </div>
                <span v-if="message.createdAt" class="message-time">{{ formatMessageTime(message.createdAt) }}</span>
              </div>

              <img
                v-if="message.imageUrl"
                :src="message.imageUrl"
                class="message-image"
                alt="上傳圖片"
                loading="lazy"
                decoding="async"
              />

              <div
                v-if="message.attachments?.length"
                class="message-attachments"
              >
                <a
                  v-for="file in message.attachments"
                  :key="file.id || `${file.name}-${file.url}`"
                  class="message-file"
                  :href="file.preview_url || file.url || undefined"
                  :target="(file.preview_url || file.url) ? '_blank' : undefined"
                  :rel="(file.preview_url || file.url) ? 'noopener noreferrer' : undefined"
                  @click.prevent="openAttachment(file.preview_url || file.url)" 
                >
                  <span class="file-icon">□</span>
                  <span class="file-info">
                    <strong>{{ file.name || '附件' }}</strong>
                    <small>{{ formatFileSize(file.size) }}</small>
                  </span>
                  <span>↗</span>
                </a>
              </div>

              <div
                v-if="message.role === 'user'"
                class="message-body user-content"
              >{{ message.text }}</div>

              <div
                v-else-if="message.reasoningSummary"
                class="reasoning-summary"
              >
                <button
                  class="reasoning-summary-toggle"
                  @click="message.reasoningExpanded = !message.reasoningExpanded"
                  :aria-expanded="message.reasoningExpanded"
                >
                  <span class="reasoning-summary-icon">✦</span>
                  <span class="reasoning-summary-title">已思考</span>
                  <span class="reasoning-summary-chevron" :class="{ open: message.reasoningExpanded }">⌄</span>
                </button>
                <div v-if="message.reasoningExpanded" class="reasoning-summary-content" v-html="renderMarkdown(message.reasoningSummary)"></div>
              </div>

              <div
                v-if="message.role !== 'user'"
                class="message-body assistant-content"
                v-html="renderMarkdown(message.text)"
              ></div>

              <div v-if="message.webSources?.length" class="web-sources">
                <div class="sources-title"><span>⌁</span> Web Search 來源</div>
                <a
                  v-for="source in message.webSources"
                  :key="source.url"
                  :href="source.url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <span class="source-title">{{ source.title || source.url }}</span>
                  <span>↗</span>
                </a>
              </div>

              <div v-if="message.role === 'assistant'" class="message-tools">
                <button @click="copyMessage(message.text)" title="複製回答">⧉ 複製</button>
              </div>
            </div>
          </div>

          <div v-if="waiting" class="message assistant typing-message">
            <div class="avatar assistant">✦</div>
            <div class="message-main">
              <div class="message-head"><strong>GGPT</strong></div>
              <div class="typing-box">
                <span></span><span></span><span></span>
                <small>{{ thinkingLabel }}</small>
              </div>
            </div>
          </div>
        </div>
      </div>

      <button
        v-if="showScrollToBottom"
        class="scroll-bottom"
        @click="scrollToBottomSmooth"
      >↓</button>

      <div
        class="composer-area"
        :class="{ 'is-dragging': draggingFiles }"
        @dragover.prevent="draggingFiles = true"
        @dragleave.self="draggingFiles = false"
        @drop.prevent="handleDrop"
      >
        <div
          v-if="pendingImage || pendingFiles.length"
          class="upload-preview"
        >
          <div v-if="pendingImage" class="preview-image-wrap">
            <img :src="pendingImage" alt="待發送圖片" decoding="async" />
            <button @click="clearPendingImage">×</button>
          </div>

          <div
            v-for="file in pendingNonImageFiles"
            :key="file.id"
            class="pending-file"
          >
            <span class="file-icon">□</span>
            <div>
              <strong>{{ file.name }}</strong>
              <small>
                {{ formatFileSize(file.size) }}
                <template v-if="file.content"> · 已讀取文字內容</template>
              </small>
            </div>
            <button @click="removePendingFile(file.id)">×</button>
          </div>

          <div class="upload-hint">
            <span>{{ uploading ? '正在上傳…' : '拖曳、選取或 Ctrl+V / ⌘V 即可附加圖片與檔案' }}</span>
          </div>
        </div>

        <div class="composer">
          <label class="tool-button upload-unified" title="上傳圖片或檔案">
            <span>＋</span>
            <input
              type="file"
              hidden
              multiple
              accept="*/*"
              @change="handleUploadInput"
            />
          </label>

          <textarea
            ref="composerTextarea"
            v-model="messageInput"
            rows="1"
            maxlength="12000"
            placeholder="傳送訊息給 GGPT…"
            @keydown="handleKeydown"
            @input="scheduleComposerResize($event.target)"
          ></textarea>

          <div class="composer-actions">
            <div class="model-selector">
              <button class="model-button" @click="modelPopoverOpen = !modelPopoverOpen">
                <span class="model-dot"></span>
                <span>{{ model }}</span>
                <span>⌄</span>
              </button>

              <div v-if="modelPopoverOpen" class="model-popover">
                <div class="model-popover-head">
                  <div>
                    <strong>模型</strong>
                    <small>選擇這次對話使用的模型</small>
                  </div>
                  <button @click="modelPopoverOpen = false">×</button>
                </div>

                <label class="web-toggle">
                  <span>
                    <strong>Web Search</strong>
                    <small>需要最新資訊時再開啟</small>
                  </span>
                  <input type="checkbox" v-model="webSearchEnabled" @change="savePreferences" />
                </label>

                <label
                  class="web-toggle reasoning-toggle"
                  :class="{ disabled: !modelSupportsReasoning() }"
                  :aria-disabled="!modelSupportsReasoning()"
                >
                  <span>
                    <strong>思考模式</strong>
                    <small>
                      {{ modelSupportsReasoning()
                        ? `目前：${reasoningEnabled ? reasoningEffort : '關閉'}`
                        : 'GPT-5 nano 不支援思考模式' }}
                    </small>
                  </span>
                  <input
                    type="checkbox"
                    :checked="modelSupportsReasoning() && reasoningEnabled"
                    :disabled="!modelSupportsReasoning()"
                    @change="toggleReasoning"
                  />
                </label>

                <div v-if="modelSupportsReasoning() && reasoningEnabled" class="reasoning-effort">
                  <div class="reasoning-effort-head">
                    <strong>思考強度</strong>
                    <small>影響推理深度與回應時間</small>
                  </div>
                  <div class="reasoning-effort-buttons">
                    <button
                      v-for="effort in availableReasoningEfforts()"
                      :key="effort"
                      type="button"
                      :class="{ selected: reasoningEffort === effort }"
                      @click="setReasoningEffort(effort)"
                    >
                      {{ effort }}
                    </button>
                  </div>
                </div>

                <button
                  v-for="item in MODEL_OPTIONS"
                  :key="item.id"
                  class="model-option"
                  :class="{ selected: model === item.id }"
                  @click="selectModel(item.id)"
                >
                  <span class="option-check">{{ model === item.id ? '✓' : '' }}</span>
                  <span>
                    <strong>{{ item.id }}</strong>
                    <small>{{ item.desc }}</small>
                    <small v-if="item.pricing" class="model-option-pricing">
                      Input / Cached / Output：{{ item.pricing }} / 1M
                    </small>
                  </span>
                </button>
              </div>
            </div>

            <button
              class="send-button"
              :disabled="waiting || (!messageInput.trim() && !pendingImage && !pendingFiles.length)"
              @click="sendMessage"
              title="送出"
            >↑</button>
          </div>
        </div>

        <div class="composer-meta">
          <span>Enter 發送 · Shift + Enter 換行 · 可拖曳或 Ctrl+V / ⌘V 上傳</span>
          <span>{{ messageInput.length.toLocaleString() }}/12,000</span>
        </div>
      </div>
    </main>

    <div class="global-chat">
      <button
        class="global-chat-toggle"
        @click="toggleGlobalChat()"
        :aria-expanded="globalChatOpen"
      >
        <span>◌</span>
        <span>共同聊天</span>
      </button>

      <div v-if="globalChatOpen" class="global-chat-panel">
        <div class="global-chat-head">
          <div>
            <strong>共同聊天</strong>
          </div>
          <button @click="toggleGlobalChat(false)">×</button>
        </div>

        <div class="global-chat-list">
          <div
            v-for="(item, index) in globalChatMessages"
            :key="item.id || index"
            class="global-chat-message"
          >
            <div class="global-chat-meta">
              <strong>{{ item.display_name || item.username || '使用者' }}</strong>
              <span>{{ formatGlobalChatTime(item.created_at || item.timestamp) }}</span>
            </div>
            <div class="global-chat-text">{{ item.message || item.text || '' }}</div>
          </div>

          <div
            v-if="!globalChatMessages.length"
            class="global-chat-empty"
          >
            還沒有訊息，來打第一句吧。
          </div>
        </div>

        <div v-if="globalChatError" class="global-chat-error">
          {{ globalChatError }}
        </div>

        <form
          class="global-chat-input"
          @submit.prevent="sendGlobalChat"
        >
          <input
            v-model="globalChatInput"
            maxlength="1000"
            placeholder="和大家說點什麼…"
          />
          <button
            type="submit"
            :disabled="globalChatSending || !globalChatInput.trim()"
          >
            ↑
          </button>
        </form>
      </div>
    </div>

    <div v-if="shareModalOpen" class="modal" @click.self="shareModalOpen = false">
      <div class="share-dialog">
        <div class="modal-header">
          <div>
            <div class="eyebrow">SHARED CONVERSATION</div>
            <h2>分享共同對話</h2>
          </div>
          <button @click="shareModalOpen = false">×</button>
        </div>

        <p class="share-description">
          取得連結的使用者登入後，就能查看並繼續使用這個對話。
        </p>

        <div v-if="shareLink" class="share-link-row">
          <input :value="shareLink" readonly @focus="$event.target.select()" />
          <button class="primary" @click="copyShareLink">
            {{ shareCopied ? '已複製' : '複製連結' }}
          </button>
        </div>

        <div v-if="shareError" class="error-message">{{ shareError }}</div>

        <button
          v-if="currentConversation?.share_enabled"
          class="revoke-share-button"
          :disabled="shareLoading"
          @click="revokeConversationShare"
        >
          停止分享並移除協作者
        </button>
      </div>
    </div>

    <div v-if="usageModal" class="modal" @click.self="usageModal = false">
      <div class="usage-dialog">
        <div class="modal-header">
          <div>
            <div class="eyebrow">USAGE</div>
            <h2>Token 使用明細</h2>
          </div>
          <button @click="usageModal = false">×</button>
        </div>

        <div v-if="usage" class="usage-grid">
          <div class="usage-stat"><small>累計 Token</small><strong>{{ Number(totalUsage.total_tokens || 0).toLocaleString() }}</strong></div>
          <div class="usage-stat"><small>思考 Token</small><strong>{{ Number(totalUsage.reasoning_tokens || 0).toLocaleString() }}</strong></div>
          <div class="usage-stat"><small>本月 Token</small><strong>{{ Number(monthUsage.total_tokens || 0).toLocaleString() }}</strong></div>
          <div class="usage-stat"><small>API 請求</small><strong>{{ Number(totalUsage.requests || 0).toLocaleString() }}</strong></div>
          <div class="usage-stat"><small>Web Search 次數</small><strong>{{ webSearchUsage.calls.toLocaleString() }}</strong></div>
          <div class="usage-stat"><small>模型使用金額</small><strong>${{ Math.max(0, Number(totalUsage.total_cost || 0) - webSearchUsage.cost).toFixed(4) }}</strong></div>
          <div class="usage-stat"><small>思考 Token 金額</small><strong>${{ Number(totalUsage.reasoning_cost || 0).toFixed(4) }}</strong></div>
          <div class="usage-stat"><small>Web Search 使用金額</small><strong>${{ webSearchUsage.cost.toFixed(4) }}</strong></div>
          <div class="usage-stat"><small>總使用金額</small><strong>${{ Number(totalUsage.total_cost || 0).toFixed(4) }}</strong></div>
          <div class="usage-stat"><small>本月總使用金額</small><strong>${{ Number(monthUsage.total_cost || 0).toFixed(4) }}</strong></div>
        </div>

        <section class="model-usage-section">
          <div class="model-usage-head">
            <div>
              <strong>各模型使用量</strong>
            </div>
          </div>

          <div v-if="modelUsageRows.length" class="model-usage-list">
            <div v-for="item in modelUsageRows" :key="item.model" class="model-usage-row">
              <div class="model-usage-name">{{ item.model }}</div>
              <div class="model-usage-value">
                <span><small>Token</small><strong>{{ item.tokens.toLocaleString() }}</strong></span>
                <span><small>思考</small><strong>{{ item.reasoningTokens.toLocaleString() }}</strong></span>
                <span><small>請求</small><strong>{{ item.requests.toLocaleString() }}</strong></span>
                <span><small>模型金額</small><strong>${{ item.cost.toFixed(4) }}</strong></span>
              </div>
            </div>
          </div>
          <div v-else class="model-usage-empty">
            目前 /usage 尚未提供各模型統計。
          </div>
        </section>

        <section class="model-usage-section">
          <div class="model-usage-head">
            <div>
              <strong>模型 Token 價格</strong>
              <small>USD／每 1M tokens：Input / Cached Input / Output</small>
            </div>
          </div>

          <div class="model-pricing-list">
            <div
              v-for="item in MODEL_OPTIONS"
              :key="`price-${item.id}`"
              class="model-pricing-row"
            >
              <span>{{ item.id }}</span>
              <strong>{{ item.pricing || '—' }}</strong>
            </div>
          </div>
        </section>

        <section class="model-usage-section web-search-usage-section">
          <div class="model-usage-head">
            <div>
              <strong>Web Search 使用量</strong>
            </div>
          </div>

          <div class="web-search-usage-row">
            <div>
              <div class="model-usage-name">Web Search</div>

            </div>
            <div class="model-usage-value">
              <span><small>使用量</small><strong>{{ webSearchUsage.calls.toLocaleString() }}</strong></span>
              <span><small>請求</small><strong>{{ webSearchUsage.requests.toLocaleString() }}</strong></span>
              <span><small>金額</small><strong>${{ webSearchUsage.cost.toFixed(4) }}</strong></span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
:global(*) { box-sizing: border-box; }
:global(html), :global(body), :global(#app) { margin: 0; min-height: 100%; }
:global(body) { background: #202123; }
:global(button), :global(input), :global(textarea) { font: inherit; }

.app {
  color-scheme: dark;
  height: 100dvh;
  display:flex;
  overflow:hidden;
  background:#202123;
  color:#ececf1;
  font-family:Inter,"Noto Sans TC",system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --panel:#171717;
  --panel-2:#212121;
  --composer:#2b2b2b;
  --border:#3c3c3c;
  --muted:#91919a;
  --text:#ececf1;
  --soft:#b9b9c0;
  --accent:#10a37f;
}

.app.light {
  color-scheme: light;
  background:#f7f8fa;
  color:#17191f;
  --panel:#ffffff;
  --panel-2:#f6f7f9;
  --composer:#ffffff;
  --border:#dde1e7;
  --muted:#737983;
  --text:#17191f;
  --soft:#454a54;
  --accent:#0f8b70;
}

.sidebar {
  width:280px;
  flex:0 0 280px;
  display:flex;
  flex-direction:column;
  background:var(--panel);
  border-right:1px solid rgba(255,255,255,.05);
  z-index:60;
}
.light .sidebar { border-right-color:#e1e4e9; }

.sidebar-top { padding:14px; }
.sidebar-brand {
  display:flex;
  align-items:center;
  gap:10px;
  padding:2px 6px 14px;
}
.brand-logo {
  width:35px; height:35px;
  display:grid; place-items:center;
  border-radius:10px;
  background:var(--accent);
  color:white;
  font-weight:900;
}
.sidebar-brand strong { display:block; font-size:14px; }
.sidebar-brand small { display:block; margin-top:2px; color:var(--muted); font-size:9px; }

.new-chat {
  width:100%;
  height:44px;
  display:flex;
  align-items:center;
  gap:8px;
  padding:0 12px;
  border:1px solid #404040;
  border-radius:11px;
  background:transparent;
  color:var(--text);
  cursor:pointer;
}
.light .new-chat { border-color:#dfe3e7; }
.new-chat:hover { background:rgba(255,255,255,.05); }
.new-chat span { font-size:18px; }
.new-chat strong { font-size:12px; }
.new-chat kbd {
  margin-left:auto;
  padding:3px 5px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:5px;
  color:var(--muted);
  font-size:8px;
}

.conversation-search {
  height:38px;
  display:flex;
  align-items:center;
  gap:8px;
  margin-top:9px;
  padding:0 10px;
  border:1px solid rgba(255,255,255,.06);
  border-radius:10px;
  background:rgba(255,255,255,.025);
}
.light .conversation-search { border-color:#e6e9ee; background:#f8f9fb; }
.conversation-search span { color:var(--muted); }
.conversation-search input {
  flex:1; min-width:0;
  border:0; outline:0;
  background:transparent;
  color:var(--text);
  font-size:11px;
}
.conversation-search button { border:0; background:transparent; color:var(--muted); cursor:pointer; }

.history-head {
  display:flex;
  justify-content:space-between;
  padding:10px 16px 7px;
  color:#77777f;
  font-size:9px;
  text-transform:uppercase;
  letter-spacing:.12em;
}
.history-head small {
  min-width:18px;
  padding:2px 5px;
  border-radius:999px;
  text-align:center;
  background:rgba(255,255,255,.06);
  letter-spacing:0;
}

.conversation-list {
  flex:1;
  min-height:0;
  overflow:auto;
  padding:0 8px;
}
.conversation-row {
  display:flex;
  align-items:center;
  margin:2px 0;
  border-radius:9px;
}
.conversation-row:hover, .conversation-row.active { background:rgba(255,255,255,.055); }
.light .conversation-row:hover, .light .conversation-row.active { background:#eef0f3; }

.conversation-button {
  flex:1; min-width:0;
  height:40px;
  display:flex;
  align-items:center;
  gap:8px;
  padding:0 10px;
  border:0;
  background:transparent;
  color:var(--soft);
  cursor:pointer;
  text-align:left;
}
.conversation-row.active .conversation-button { color:var(--text); }
.conversation-icon { color:var(--muted); font-size:12px; }
.conversation-title {
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
  font-size:13px;
}
.conversation-menu {
  width:30px;
  height:30px;
  margin-right:4px;
  border:0;
  border-radius:8px;
  background:transparent;
  color:#717178;
  cursor:pointer;
  opacity:0;
}
.conversation-row:hover .conversation-menu { opacity:1; }
.conversation-menu:hover { background:rgba(255,255,255,.06); color:#fff; }

.sidebar-empty { padding:26px 12px; color:#696a71; text-align:center; font-size:12px; }
.history-load-more {
  display:block;
  width:calc(100% - 12px);
  margin:8px auto;
  padding:8px 10px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:9px;
  background:rgba(255,255,255,.035);
  color:var(--muted);
  cursor:pointer;
  font-size:11px;
}
.history-load-more:hover { color:var(--text); background:rgba(255,255,255,.065); }
.light .history-load-more { border-color:#e2e4e8; background:#f7f8fa; }
.message-load-more { width:auto; min-width:210px; margin:4px auto 20px; }

.usage-card {
  margin:10px 11px;
  padding:12px;
  border:1px solid rgba(255,255,255,.07);
  border-radius:13px;
  background:rgba(255,255,255,.03);
  cursor:pointer;
}
.light .usage-card { border-color:#e3e5ea; background:#f8f9fb; }
.usage-top { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; }
.usage-top span, .usage-bottom { color:var(--muted); font-size:11px; }
.usage-top strong { display:block; margin-top:5px; color:var(--text); font-size:18px; }
.usage-link { color:#77cdb9 !important; cursor:pointer; }
.usage-bar { height:5px; margin:10px 0 8px; border-radius:999px; overflow:hidden; background:#353535; }
.light .usage-bar { background:#dde1e7; }
.usage-bar span { display:block; height:100%; border-radius:inherit; background:linear-gradient(90deg,#10a37f,#53d8bb); }
.usage-bottom { display:flex; justify-content:space-between; }
.usage-search-line {
  display:flex;
  justify-content:space-between;
  gap:8px;
  margin-top:8px;
  padding-top:8px;
  border-top:1px solid rgba(255,255,255,.06);
  color:var(--muted);
  font-size:10px;
}
.light .usage-search-line { border-top-color:#e6e8ed; }

.sidebar-bottom { padding:9px; border-top:1px solid rgba(255,255,255,.06); }
.light .sidebar-bottom { border-top-color:#e6e8ed; }
.side-tool {
  width:100%; height:35px;
  display:flex; align-items:center; gap:8px;
  margin-bottom:7px; padding:0 9px;
  border:0; border-radius:8px;
  background:transparent; color:var(--soft);
  font-size:10px; cursor:pointer;
}
.side-tool:hover { background:rgba(255,255,255,.04); }

.account {
  display:flex;
  align-items:center;
  gap:9px;
  padding:8px 4px 2px;
}
.account-avatar {
  width:31px; height:31px;
  display:grid; place-items:center;
  border-radius:9px;
  background:#3c3c3c;
  color:white;
  font-size:11px; font-weight:800;
}
.light .account-avatar { background:#e6e8eb; color:#333; }
.account-info { flex:1; min-width:0; }
.account-info strong, .account-info small { display:block; }
.account-info strong { color:var(--text); font-size:12px; }
.account-info small { margin-top:2px; color:var(--muted); font-size:10px; }
.logout { border:0; background:transparent; color:var(--muted); font-size:17px; cursor:pointer; }

.main { min-width:0; flex:1; display:flex; flex-direction:column; position:relative; }

.topbar {
  height:60px;
  flex:0 0 60px;
  display:flex;
  align-items:center;
  padding:0 17px;
  border-bottom:1px solid rgba(255,255,255,.05);
  background:color-mix(in srgb, var(--panel-2) 94%, transparent);
  backdrop-filter:blur(15px);
  z-index:25;
}
.light .topbar { border-bottom-color:#e3e6eb; }

.mobile-button {
  display:none;
  width:36px; height:36px;
  margin-right:9px;
  border:0; border-radius:9px;
  background:transparent; color:var(--text);
  cursor:pointer;
}

.chat-title { min-width:0; display:flex; align-items:baseline; gap:8px; }
.chat-title strong {
  max-width:460px; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap;
  font-size:15px;
}
.chat-title span {
  color:var(--muted);
  font-size:11px;
  max-width:230px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}

.top-actions { margin-left:auto; display:flex; gap:4px; }
.icon-button {
  width:34px; height:34px;
  border:0; border-radius:9px;
  background:transparent; color:var(--muted);
  cursor:pointer;
}
.icon-button:hover { background:rgba(255,255,255,.05); color:var(--text); }

.chat-scroll { flex:1; min-height:0; overflow:auto; }
.chat-box { width:min(920px,100%); margin:0 auto; padding:22px 19px 150px; }

.welcome {
  min-height:calc(100dvh - 260px);
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  text-align:center;
}
.welcome-mark {
  width:58px; height:58px;
  display:grid; place-items:center;
  border-radius:18px;
  background:var(--accent);
  color:white;
  font-size:24px;
  box-shadow:0 15px 35px rgba(16,163,127,.18);
}
.welcome .eyebrow { margin-top:18px; color:var(--muted); font-size:11px; letter-spacing:.15em; }
.welcome h1 { margin:8px 0 7px; font-size:32px; letter-spacing:-.03em; color:var(--text); }
.welcome p { max-width:600px; margin:0; color:var(--muted); font-size:14px; line-height:1.8; }

.suggestions {
  width:min(720px,100%);
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:9px;
  margin-top:23px;
}
.suggestions button {
  min-height:78px;
  display:flex;
  align-items:center;
  gap:10px;
  padding:12px;
  border:1px solid rgba(255,255,255,.07);
  border-radius:13px;
  background:rgba(255,255,255,.025);
  color:var(--text);
  text-align:left;
  cursor:pointer;
  transition:.18s ease;
}
.light .suggestions button { border-color:#e2e5e9; background:#fff; }
.suggestions button:hover { transform:translateY(-1px); background:rgba(255,255,255,.045); }
.suggestion-icon {
  width:34px; height:34px; flex:0 0 34px;
  display:grid; place-items:center;
  border-radius:10px;
  background:rgba(16,163,127,.12);
  color:#67cfba;
  font-weight:800;
}
.suggestions strong { display:block; font-size:11px; }
.suggestions small { display:block; margin-top:4px; color:var(--muted); font-size:9px; line-height:1.5; }

.message {
  display:flex;
  gap:13px;
  padding:19px 0;
}
.message + .message { border-top:1px solid rgba(255,255,255,.03); }
.light .message + .message { border-top-color:#eceef1; }

.avatar {
  width:33px; height:33px; flex:0 0 33px;
  display:grid; place-items:center;
  border-radius:10px;
  font-size:11px;
  font-weight:800;
}
.avatar.user { background:#3a3a3d; color:#fff; }
.avatar.assistant { background:var(--accent); color:#fff; }
.light .avatar.user { background:#e7e9ed; color:#30343b; }

.message-main { flex:1; min-width:0; }
.message-head {
  display:flex; align-items:center; gap:8px;
  min-height:20px;
  margin-bottom:7px;
}
.message-author { display:flex; align-items:center; gap:7px; }
.message-author strong { font-size:11px; color:var(--text); }
.message-time { margin-left:auto; color:#6c6d73; font-size:8px; }

.model-badge {
  padding:3px 6px;
  border:1px solid rgba(16,163,127,.18);
  border-radius:999px;
  background:rgba(16,163,127,.08);
  color:#7ed9c5;
  font-size:8px;
}

.message-body {
  color:var(--text);
  font-size:14px;
  line-height:1.83;
  overflow-wrap:anywhere;
}
.user-content {
  display:inline-block;
  max-width:min(780px,100%);
  padding:9px 12px;
  border-radius:13px;
  background:rgba(255,255,255,.055);
  white-space:pre-wrap;
}
.light .user-content { background:#f0f2f5; }

.message-image {
  width:auto;
  max-width:min(520px,100%);
  max-height:420px;
  display:block;
  margin:0 0 10px;
  border-radius:13px;
  border:1px solid rgba(255,255,255,.07);
}

.assistant-content :deep(p:first-child) { margin-top:0; }
.assistant-content :deep(p:last-child) { margin-bottom:0; }
.assistant-content :deep(pre) {
  margin:12px 0;
  overflow:auto;
  border:1px solid rgba(255,255,255,.075);
  border-radius:13px;
  background:#101113;
  box-shadow:0 8px 24px rgba(0,0,0,.10);
  position:relative;
}
.assistant-content :deep(.code-toolbar) {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  min-height:36px;
  padding:5px 8px 5px 12px;
  border-bottom:1px solid rgba(255,255,255,.055);
  background:rgba(255,255,255,.022);
  backdrop-filter:blur(8px);
}
.assistant-content :deep(.code-language) {
  display:inline-flex;
  align-items:center;
  min-width:0;
  color:#8f95a0;
  font-size:10px;
  font-weight:600;
  text-transform:lowercase;
  letter-spacing:.02em;
  user-select:none;
  overflow:hidden;
  text-overflow:ellipsis;
}
.assistant-content :deep(.code-actions) {
  display:flex;
  align-items:center;
  gap:2px;
  flex:none;
}
.assistant-content :deep(.code-action-button) {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  gap:5px;
  height:27px;
  padding:0 8px;
  border:1px solid transparent;
  border-radius:7px;
  background:transparent;
  color:#858b96;
  font:500 10px/1 inherit;
  cursor:pointer;
  opacity:.72;
  appearance:none;
  -webkit-appearance:none;
  box-shadow:none;
  outline:none;
  transition:background .16s ease,border-color .16s ease,color .16s ease,opacity .16s ease,transform .08s ease;
}
.assistant-content :deep(.code-action-button:hover) {
  background:rgba(255,255,255,.065);
  border-color:rgba(255,255,255,.07);
  color:#eceff3;
  opacity:1;
}
.assistant-content :deep(.code-action-button:active) { transform:translateY(1px); }
.assistant-content :deep(.code-action-button:focus-visible) {
  border-color:rgba(255,255,255,.16);
  box-shadow:0 0 0 2px rgba(255,255,255,.05);
}
.assistant-content :deep(.code-action-button.is-copied) {
  background:rgba(16,163,127,.11);
  border-color:rgba(16,163,127,.18);
  color:#7ed9c5;
  opacity:1;
}
.assistant-content :deep(.copy-icon),
.assistant-content :deep(.download-icon) {
  position:relative;
  width:12px;
  height:12px;
  flex:none;
  opacity:.92;
}
.assistant-content :deep(.copy-icon::before),
.assistant-content :deep(.copy-icon::after) {
  content:'';
  position:absolute;
  border:1.35px solid currentColor;
  border-radius:2px;
  width:7px;
  height:7px;
}
.assistant-content :deep(.copy-icon::before) { left:3.5px; top:1px; opacity:.62; }
.assistant-content :deep(.copy-icon::after) { left:1px; top:3.5px; background:transparent; }
.assistant-content :deep(.download-icon::before) {
  content:'';
  position:absolute;
  left:3px;
  top:1px;
  width:5px;
  height:7px;
  border-right:1.4px solid currentColor;
  border-bottom:1.4px solid currentColor;
  transform:rotate(45deg);
}
.assistant-content :deep(.download-icon::after) {
  content:'';
  position:absolute;
  left:1.5px;
  bottom:0;
  width:9px;
  height:1.4px;
  border-radius:2px;
  background:currentColor;
}
.assistant-content :deep(pre:hover .code-action-button) { opacity:.96; }
.assistant-content :deep(.code-toolbar[data-has-download="0"] .code-copy-button) {
  padding:0 7px;
  opacity:.58;
}
.assistant-content :deep(.code-toolbar[data-has-download="0"] .code-copy-button:hover) { opacity:1; }
.light .assistant-content :deep(.code-action-button) { color:#6f7680; }
.light .assistant-content :deep(.code-action-button:hover) {
  background:rgba(0,0,0,.055);
  border-color:rgba(0,0,0,.07);
  color:#1d2025;
}
.light .assistant-content :deep(.code-action-button.is-copied) {
  background:rgba(16,163,127,.10);
  border-color:rgba(16,163,127,.16);
  color:#16856e;
}
.light .assistant-content :deep(.code-toolbar) {
  border-bottom-color:rgba(0,0,0,.075);
  background:rgba(0,0,0,.025);
}
.assistant-content :deep(code) { font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace; font-size:12px; }
.assistant-content :deep(:not(pre) > code) {
  padding:2px 5px;
  border-radius:5px;
  background:rgba(255,255,255,.06);
}
.assistant-content :deep(blockquote) {
  margin:11px 0;
  padding:2px 0 2px 12px;
  border-left:3px solid var(--accent);
  color:#b8b8bf;
}
.assistant-content :deep(table) { width:100%; overflow:auto; display:block; border-collapse:collapse; }
.assistant-content :deep(th), .assistant-content :deep(td) { padding:7px 9px; border:1px solid #444; }
.assistant-content :deep(a) { color:#74b7ff; }

.web-sources {
  margin-top:13px;
  padding-top:10px;
  border-top:1px solid rgba(255,255,255,.05);
}
.sources-title {
  display:flex; align-items:center; gap:6px;
  margin-bottom:7px;
  color:#8790a0;
  font-size:9px;
}
.web-sources a {
  display:flex;
  justify-content:space-between;
  gap:8px;
  margin-top:5px;
  padding:8px 9px;
  border:1px solid rgba(255,255,255,.05);
  border-radius:9px;
  background:rgba(255,255,255,.02);
  color:#a9ccf4;
  text-decoration:none;
  font-size:9px;
}
.web-sources a:hover { background:rgba(255,255,255,.04); }
.source-title { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

.message-tools {
  display:flex;
  align-items:center;
  gap:5px;
  margin-top:8px;
}
.message-tools button {
  display:inline-flex;
  align-items:center;
  height:28px;
  padding:0 9px;
  border:1px solid transparent;
  border-radius:8px;
  background:transparent;
  color:#74757c;
  font:500 9px/1 inherit;
  cursor:pointer;
  transition:background .16s ease,color .16s ease,border-color .16s ease;
}
.message-tools button:hover {
  background:rgba(255,255,255,.045);
  border-color:rgba(255,255,255,.065);
  color:var(--text);
}
.light .message-tools button:hover {
  background:rgba(0,0,0,.045);
  border-color:rgba(0,0,0,.065);
}

.typing-box { display:flex; align-items:center; gap:4px; }
.typing-box > span {
  width:6px; height:6px;
  border-radius:50%;
  background:#9a9aa0;
  animation:typing .9s infinite;
}
.typing-box > span:nth-child(2) { animation-delay:.15s; }
.typing-box > span:nth-child(3) { animation-delay:.3s; }
.typing-box small { margin-left:6px; color:#787980; font-size:9px; }
@keyframes typing { 0%,80%,100%{opacity:.4;transform:translateY(0)} 40%{opacity:1;transform:translateY(-4px)} }

.scroll-bottom {
  position:absolute;
  right:24px;
  bottom:128px;
  width:34px; height:34px;
  border:1px solid rgba(255,255,255,.09);
  border-radius:50%;
  background:#2c2c2f;
  color:#fff;
  box-shadow:0 10px 25px rgba(0,0,0,.2);
  cursor:pointer;
  z-index:18;
}

.composer-area {
  padding:11px 14px 13px;
  background:linear-gradient(180deg,rgba(32,33,35,0),var(--panel-2) 20%);
}
.light .composer-area { background:linear-gradient(180deg,rgba(247,248,250,0),#f7f8fa 20%); }
.composer {
  width:min(920px,100%);
  min-height:58px;
  display:flex;
  align-items:flex-end;
  gap:7px;
  margin:0 auto;
  padding:7px;
  border:1px solid #4a4a4d;
  border-radius:18px;
  background:var(--composer);
  box-shadow:0 14px 35px rgba(0,0,0,.12);
}
.light .composer { border-color:#d9dde3; box-shadow:0 12px 26px rgba(18,25,35,.07); }

.tool-button {
  width:38px; height:38px;
  flex:0 0 38px;
  display:grid; place-items:center;
  border-radius:10px;
  background:transparent;
  color:#9a9aa1;
  cursor:pointer;
}
.tool-button:hover { background:rgba(255,255,255,.05); color:var(--text); }

.composer textarea {
  flex:1;
  min-width:0;
  max-height:190px;
  padding:10px 4px 8px;
  border:0; outline:0; resize:none;
  background:transparent;
  color:var(--text);
  line-height:1.6;
  font-size:15px;
}
.composer textarea::placeholder { color:#777980; }

.composer-actions { display:flex; align-items:flex-end; gap:5px; }
.model-selector { position:relative; }
.model-button {
  max-width:210px; height:38px;
  display:flex; align-items:center; gap:6px;
  padding:0 9px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:10px;
  background:rgba(255,255,255,.025);
  color:var(--soft);
  cursor:pointer;
  font-size:9px;
}
.light .model-button { border-color:#e0e3e8; }
.model-button span:nth-child(2) { max-width:135px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.model-dot { width:6px; height:6px; border-radius:50%; background:#63dbb8; }

.model-popover {
  width:340px;
  position:absolute;
  right:0;
  bottom:48px;
  padding:10px;
  border:1px solid rgba(255,255,255,.09);
  border-radius:14px;
  background:#232326;
  box-shadow:0 25px 65px rgba(0,0,0,.34);
  z-index:50;
}
.light .model-popover { background:#fff; border-color:#dde1e7; box-shadow:0 25px 65px rgba(0,0,0,.14); }
.model-popover-head {
  display:flex; align-items:flex-start; justify-content:space-between;
  padding:5px 6px 9px;
}
.model-popover-head strong { display:block; font-size:11px; color:var(--text); }
.model-popover-head small { display:block; margin-top:3px; color:var(--muted); font-size:8px; }
.model-popover-head button { border:0; background:transparent; color:var(--muted); cursor:pointer; }

.web-toggle {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  padding:10px;
  margin-bottom:5px;
  border:1px solid rgba(255,255,255,.05);
  border-radius:10px;
  background:rgba(255,255,255,.028);
  cursor:pointer;
}
.light .web-toggle { border-color:#eceef1; background:#f7f8fa; }
.web-toggle strong { display:block; color:var(--text); font-size:10px; }
.web-toggle small { display:block; margin-top:3px; color:var(--muted); font-size:8px; }
.web-toggle input { width:16px; height:16px; accent-color:var(--accent); }
.reasoning-toggle.disabled { opacity:.48; cursor:not-allowed; }
.reasoning-toggle.disabled input { cursor:not-allowed; }
.reasoning-effort {
  margin:0 0 7px;
  padding:9px 10px;
  border:1px solid rgba(255,255,255,.05);
  border-radius:10px;
  background:rgba(255,255,255,.02);
}
.light .reasoning-effort { border-color:#eceef1; background:#f7f8fa; }
.reasoning-effort-head strong { display:block; color:var(--text); font-size:10px; }
.reasoning-effort-head small { display:block; margin-top:3px; color:var(--muted); font-size:8px; }
.reasoning-effort-buttons {
  display:flex;
  flex-wrap:wrap;
  gap:5px;
  margin-top:7px;
}
.reasoning-effort-buttons button {
  border:1px solid rgba(255,255,255,.08);
  border-radius:7px;
  padding:5px 8px;
  background:transparent;
  color:var(--muted);
  font-size:9px;
  cursor:pointer;
}
.reasoning-effort-buttons button.selected {
  border-color:rgba(99,219,184,.45);
  color:var(--text);
  background:rgba(99,219,184,.10);
}
.light .reasoning-effort-buttons button { border-color:#dfe3e8; }
.light .reasoning-effort-buttons button.selected {
  border-color:rgba(15,139,112,.35);
  background:rgba(15,139,112,.08);
}
.model-option-pricing { color:var(--muted); opacity:.86; }

.model-option {
  width:100%;
  display:flex; align-items:flex-start; gap:9px;
  padding:10px;
  border:0;
  border-radius:9px;
  background:transparent;
  color:var(--text);
  text-align:left;
  cursor:pointer;
}
.model-option:hover, .model-option.selected { background:rgba(255,255,255,.05); }
.light .model-option:hover, .light .model-option.selected { background:#f1f3f5; }
.option-check { width:13px; color:#64dcb9; font-size:11px; }
.model-option strong { display:block; font-size:10px; }
.model-option small { display:block; margin-top:3px; color:var(--muted); font-size:8px; }

.send-button {
  width:38px; height:38px;
  flex:0 0 38px;
  border:0; border-radius:11px;
  background:#fff; color:#000;
  font-size:17px;
  cursor:pointer;
}
.send-button:disabled { opacity:.28; cursor:default; }
.light .send-button { background:#17191d; color:#fff; }

.composer-meta {
  width:min(920px,100%);
  display:flex;
  justify-content:space-between;
  gap:10px;
  margin:7px auto 0;
  padding:0 2px;
  color:#6e7077;
  font-size:10px;
}

.upload-preview {
  width:min(920px,100%);
  display:flex;
  align-items:center;
  gap:9px;
  margin:0 auto 8px;
  padding:8px;
  border:1px solid rgba(255,255,255,.07);
  border-radius:12px;
  background:rgba(255,255,255,.028);
}
.upload-preview img { width:48px; height:48px; object-fit:cover; border-radius:8px; }
.upload-preview div { flex:1; min-width:0; }
.upload-preview strong, .upload-preview small { display:block; }
.upload-preview strong { color:var(--text); font-size:9px; }
.upload-preview small { margin-top:3px; color:var(--muted); font-size:8px; }
.upload-preview button {
  width:28px; height:28px;
  border:0; border-radius:8px;
  background:transparent; color:var(--muted);
  cursor:pointer;
}

.auth-page {
  min-height:100dvh;
  display:grid;
  place-items:center;
  padding:22px;
  position:relative;
  overflow:hidden;
  background:#111217;
  color:#ececf1;
}
.auth-card {
  width:min(440px,100%);
  position:relative;
  z-index:2;
  padding:31px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:20px;
  background:rgba(32,33,35,.94);
  box-shadow:0 30px 100px rgba(0,0,0,.3);
  backdrop-filter:blur(18px);
}
.auth-brand { display:flex; align-items:center; gap:10px; }
.auth-brand strong { display:block; font-size:15px; }
.auth-brand small { display:block; margin-top:2px; color:#7d7f86; font-size:9px; }
.auth-logo {
  width:38px; height:38px;
  display:grid; place-items:center;
  border-radius:11px;
  background:var(--accent);
  color:#fff; font-size:17px;
}
.auth-copy { margin-top:25px; }
.auth-copy .eyebrow { color:#71747d; font-size:9px; letter-spacing:.14em; }
.auth-copy h1 { margin:7px 0; font-size:27px; letter-spacing:-.03em; }
.auth-copy p { margin:0; color:#8b8e96; font-size:11px; line-height:1.75; }
.auth-copy .share-invite-notice {
  margin-top:12px;
  padding:10px 11px;
  border:1px solid rgba(16,163,127,.25);
  border-radius:9px;
  background:rgba(16,163,127,.08);
  color:#9bdacb;
}

.auth-form { margin-top:19px; }
.auth-form label { display:block; margin:12px 0 6px; color:#9698a1; font-size:11px; }
.auth-field {
  height:45px;
  display:flex; align-items:center; gap:8px;
  padding:0 11px;
  border:1px solid #414146;
  border-radius:10px;
  background:#191a1d;
}
.auth-field:focus-within { border-color:#64666d; }
.auth-field span { color:#686a72; font-size:10px; }
.auth-field input { flex:1; min-width:0; border:0; outline:0; background:transparent; color:#ececf1; font-size:14px; }
.auth-rule-hint { display:block; margin-top:8px; color:#777983; font-size:11px; line-height:1.5; }
.primary, .secondary {
  width:100%; height:44px;
  border-radius:10px;
  cursor:pointer;
}
.primary { border:1px solid #44454a; background:#26272a; color:#fff; }
.secondary { border:1px solid #44454a; background:#26272a; color:#e1e2e6; margin-top:8px; }
.auth-submit { margin-top:13px; font-weight:800; }
.auth-register { font-size:11px; }
.error-message {
  margin-top:10px;
  padding:10px 11px;
  border:1px solid rgba(239,68,68,.24);
  border-radius:9px;
  background:rgba(239,68,68,.08);
  color:#f3a4aa;
  font-size:11px;
}
.auth-divider {
  display:flex; align-items:center; gap:9px;
  margin:18px 0;
  color:#6e7078; font-size:8px;
}
.auth-divider span { flex:1; height:1px; background:#3b3c40; }
.auth-divider em { font-style:normal; }
.google-login { display:flex; justify-content:center; min-height:40px; }
.auth-footer {
  display:flex; align-items:center; gap:7px;
  margin-top:18px; color:#5f626b; font-size:8px;
}
.auth-orb { position:absolute; border-radius:50%; filter:blur(80px); opacity:.18; }
.orb-a { width:330px; height:330px; top:-150px; left:-90px; background:#5ee7c1; }
.orb-b { width:360px; height:360px; right:-160px; bottom:-180px; background:#4e93ff; }
.spinner {
  width:13px; height:13px;
  display:inline-block;
  margin-right:6px;
  border:2px solid rgba(255,255,255,.28);
  border-top-color:#fff;
  border-radius:50%;
  animation:spin .8s linear infinite;
}
@keyframes spin { to { transform:rotate(360deg); } }

.modal {
  position:fixed;
  inset:0;
  z-index:100;
  display:grid;
  place-items:center;
  padding:18px;
  background:rgba(0,0,0,.66);
  backdrop-filter:blur(7px);
}
.usage-dialog {
  width:min(720px,100%);
  padding:19px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:18px;
  background:#252527;
  color:#ececf1;
  box-shadow:0 30px 90px rgba(0,0,0,.35);
}
.share-dialog {
  width:min(560px,100%);
  padding:20px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:18px;
  background:#252527;
  color:#ececf1;
  box-shadow:0 30px 90px rgba(0,0,0,.35);
}
.light .share-dialog { background:#fff; color:#181a20; border-color:#dde1e7; }
.share-description { margin:16px 0 12px; color:var(--muted); font-size:12px; line-height:1.7; }
.share-link-row { display:flex; gap:8px; }
.share-link-row input {
  flex:1;
  min-width:0;
  height:42px;
  padding:0 11px;
  border:1px solid rgba(255,255,255,.1);
  border-radius:9px;
  outline:0;
  background:#191a1d;
  color:#dfe1e6;
  font-size:11px;
}
.light .share-link-row input { border-color:#dfe3e9; background:#f7f8fa; color:#22252b; }
.share-link-row .primary { width:auto; min-width:96px; padding:0 14px; }
.revoke-share-button {
  margin-top:18px;
  padding:8px 0;
  border:0;
  background:transparent;
  color:#ef8d94;
  cursor:pointer;
  font-size:11px;
}
.revoke-share-button:disabled, .share-button:disabled { cursor:not-allowed; opacity:.5; }
.light .usage-dialog { background:#fff; color:#181a20; border-color:#dde1e7; }
.modal-header { display:flex; justify-content:space-between; align-items:flex-start; }
.modal-header h2 { margin:5px 0 0; font-size:19px; }
.modal-header button { border:0; background:transparent; color:#868890; font-size:22px; cursor:pointer; }
.usage-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:9px; margin-top:16px; }
.usage-stat {
  min-width:0;
  padding:12px;
  border:1px solid rgba(255,255,255,.06);
  border-radius:11px;
  background:rgba(255,255,255,.025);
}
.light .usage-stat { border-color:#e6e9ed; background:#f8f9fb; }
.usage-stat small { display:block; color:#858790; font-size:11px; }
.usage-stat strong { display:block; margin-top:6px; color:inherit; font-size:20px; }
.usage-stat-total {
  border-color:rgba(16,163,127,.24);
  background:rgba(16,163,127,.06);
}
.light .usage-stat-total {
  border-color:rgba(15,139,112,.20);
  background:rgba(15,139,112,.05);
}
.math-display { overflow-x:auto; margin:10px 0; }

.model-usage-section {
  margin-top:18px;
  padding-top:18px;
  border-top:1px solid rgba(255,255,255,.08);
}
.light .model-usage-section { border-top-color:#e5e8ed; }
.model-usage-head { display:flex; justify-content:space-between; gap:12px; margin-bottom:10px; }
.model-usage-head strong { display:block; font-size:14px; }
.model-usage-head small { display:block; margin-top:4px; color:var(--muted); font-size:10px; }
.model-usage-list { display:flex; flex-direction:column; gap:8px; }
.model-usage-row {
  display:grid;
  grid-template-columns:minmax(150px,1fr) auto;
  gap:14px; align-items:center;
  padding:12px; border:1px solid rgba(255,255,255,.07); border-radius:11px; background:rgba(255,255,255,.025);
}
.light .model-usage-row { border-color:#e5e8ed; background:#f8f9fb; }
.model-usage-name { color:var(--text); font-weight:700; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.model-usage-value { display:flex; align-items:center; gap:18px; }
.model-usage-value span { min-width:76px; text-align:right; }
.model-usage-value small { display:block; color:var(--muted); font-size:9px; }
.model-usage-value strong { display:block; margin-top:2px; color:var(--text); font-size:12px; }
.model-usage-empty { padding:14px; border-radius:10px; background:rgba(255,255,255,.025); color:var(--muted); font-size:11px; line-height:1.7; }
.model-usage-empty code { padding:1px 4px; border-radius:4px; background:rgba(127,127,127,.15); font-size:.95em; }
.model-pricing-list { display:flex; flex-direction:column; gap:6px; }
.model-pricing-row {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  padding:9px 11px;
  border:1px solid rgba(255,255,255,.06);
  border-radius:9px;
  background:rgba(255,255,255,.02);
  font-size:10px;
}
.light .model-pricing-row { border-color:#e5e8ed; background:#f8f9fb; }
.model-pricing-row span { color:var(--text); font-weight:600; }
.model-pricing-row strong { color:var(--muted); font-size:9px; font-weight:500; text-align:right; }
.reasoning-summary {
  width:min(700px,100%);
  margin:0 0 10px;
  border:1px solid rgba(255,255,255,.07);
  border-radius:10px;
  background:rgba(255,255,255,.025);
  overflow:hidden;
}
.light .reasoning-summary { border-color:#e4e7eb; background:#fafbfc; }
.reasoning-summary-toggle {
  width:100%;
  display:flex;
  align-items:center;
  gap:7px;
  padding:8px 10px;
  border:0;
  background:transparent;
  color:var(--muted);
  text-align:left;
  cursor:pointer;
}
.reasoning-summary-toggle:hover { background:rgba(255,255,255,.025); }
.light .reasoning-summary-toggle:hover { background:#f3f5f7; }
.reasoning-summary-icon { font-size:10px; color:var(--accent); }
.reasoning-summary-title { font-size:10px; font-weight:600; color:var(--text); }
.reasoning-summary-chevron { margin-left:auto; font-size:11px; transition:transform .18s ease; }
.reasoning-summary-chevron.open { transform:rotate(180deg); }
.reasoning-summary-content {
  padding:0 12px 11px 27px;
  color:var(--muted);
  font-size:11px;
  line-height:1.65;
  border-top:1px solid rgba(255,255,255,.05);
}
.light .reasoning-summary-content { border-top-color:#e9ecef; }
.reasoning-summary-content > :first-child { margin-top:9px; }
.reasoning-summary-content > :last-child { margin-bottom:0; }

.web-search-usage-section { margin-top:14px; }
.web-search-usage-row {
  display:grid;
  grid-template-columns:minmax(150px,1fr) auto;
  gap:14px;
  align-items:center;
  padding:12px;
  border:1px solid rgba(101, 121, 116, 0.2);
  border-radius:11px;
  background:rgba(69, 78, 76, 0.05);
}
.light .web-search-usage-row {
  border-color:rgba(15,139,112,.18);
  background:rgba(15,139,112,.04);
}
.web-search-note {
  display:block;
  margin-top:4px;
  color:var(--muted);
  font-size:10px;
}

.katex { font-size:1.08em; }

.sidebar-backdrop { display:none; }

@media (max-width:850px), (pointer: coarse) {
  .sidebar {
    position:fixed;
    top:0; left:0; bottom:0;
    transform:translateX(-105%);
    transition:transform .22s ease;
    box-shadow:24px 0 70px rgba(0,0,0,.32);
  }
  .sidebar.open { transform:translateX(0); }
  .sidebar-backdrop {
    display:block;
    position:fixed;
    inset:0;
    z-index:50;
    background:rgba(0,0,0,.35);
    backdrop-filter:blur(2px);
  }
  .mobile-button { display:grid; place-items:center; }
  .chat-box { padding:16px 14px 145px; }
  .welcome { min-height:calc(100dvh - 230px); }
  .welcome h1 { font-size:26px; }
  .suggestions { grid-template-columns:1fr; max-width:560px; }
  .model-button { max-width:160px; }
}

@media (max-width:620px) {
  .topbar { padding:0 10px; }
  .chat-title span { display:none; }
  .chat-title strong { max-width:210px; }
  .message { gap:9px; padding:15px 0; }
  .avatar { width:30px; height:30px; flex-basis:30px; border-radius:9px; }
  .message-body { font-size:15px; }
  .composer { border-radius:16px; }
  .model-button span:nth-child(2) { max-width:90px; }
  .model-popover { width:min(320px, calc(100vw - 20px)); }
  .usage-grid { grid-template-columns:repeat(2,1fr); }
  .model-usage-row { grid-template-columns:1fr; }
  .model-usage-value { flex-wrap:wrap; gap:12px; justify-content:space-between; }
  .model-usage-value span { min-width:auto; text-align:left; }
  .composer-meta span:last-child { display:none; }
}


/* FONT SCALE OVERRIDES */
.sidebar,
.main,
.auth-card,
.usage-dialog,
.model-popover,
.composer-wrap {
  font-size: 16px;
}
.history-title { font-size: 13px !important; }
.conversation-button { font-size: 14px !important; }
.usage-top span, .usage-bottom { font-size: 13px !important; }
.usage-top strong { font-size: 20px !important; }
.account-info strong { font-size: 14px !important; }
.account-info small { font-size: 12px !important; }
.chat-title strong { font-size: 17px !important; }
.chat-title span { font-size: 13px !important; }
.welcome .eyebrow { font-size: 13px !important; }
.welcome h1 { font-size: 35px !important; }
.welcome p { font-size: 16px !important; }
.suggestions strong { font-size: 13px !important; }
.suggestions small { font-size: 11px !important; }
.message-author strong { font-size: 13px !important; }
.message-time { font-size: 10px !important; }
.message-body { font-size: 16px !important; }
.assistant-content :deep(code) { font-size: 13px !important; }
.web-sources strong { font-size: 13px !important; }
.web-sources a { font-size: 13px !important; }
.typing-box small { font-size: 11px !important; }
.composer textarea { font-size: 17px !important; }
.model-popover-head strong { font-size: 13px !important; }
.model-popover-head small { font-size: 11px !important; }
.web-toggle strong { font-size: 12px !important; }
.web-toggle small { font-size: 11px !important; }
.model-option strong { font-size: 12px !important; }
.model-option small { font-size: 11px !important; }
.upload-preview strong { font-size: 12px !important; }
.upload-preview small { font-size: 11px !important; }
.auth-brand strong { font-size: 17px !important; }
.auth-brand small { font-size: 11px !important; }
.auth-copy .eyebrow { font-size: 11px !important; }
.auth-copy h1 { font-size: 30px !important; }
.auth-copy p { font-size: 13px !important; }
.auth-form label { font-size: 13px !important; }
.auth-field span { font-size: 12px !important; }
.auth-field input { font-size: 16px !important; }
.auth-register { font-size: 13px !important; }
.usage-stat small { font-size: 13px !important; }
.usage-stat strong { font-size: 22px !important; }
.model-usage-head strong { font-size: 16px !important; }
.model-usage-head small { font-size: 12px !important; }
.model-usage-name { font-size: 14px !important; }
.model-usage-value small { font-size: 11px !important; }
.model-usage-value strong { font-size: 14px !important; }
.model-usage-empty { font-size: 13px !important; }
.composer-hint { font-size: 12px !important; }
@media (max-width:620px) {
  .message-body { font-size: 16px !important; }
  .welcome h1 { font-size: 29px !important; }
}


/* ===== LIGHT THEME FIXES: controls that were still using dark-mode hardcoded colors ===== */
.light .sidebar-bottom {
  background: var(--panel);
  border-top-color: #e6e8ed;
}

.light .new-chat {
  border-color: #dfe3e7;
  background: #ffffff;
}

.light .new-chat:hover {
  border-color: #cfd4da;
  background: #f3f4f6;
}

.light .new-chat kbd {
  border-color: #d7dbe1;
  background: #f1f3f5;
  color: #656b74;
  box-shadow: inset 0 -1px 0 rgba(20, 24, 30, .05);
}

.light .account {
  border-radius: 10px;
  background: #ffffff;
}

.light .account-avatar {
  border-color: #d9dde3;
  background: #eef0f3;
  color: #30343b;
}

.light .side-tool:hover,
.light .logout:hover {
  background: #f0f1f3;
}

.light .model-button {
  border-color: #dde1e7;
  background: #f3f4f6;
  color: #454a54;
}

.light .model-button:hover {
  border-color: #cfd4da;
  background: #e9ecef;
}

.light .tool-button {
  background: #f3f4f6;
  color: #6f7680;
}

.light .tool-button:hover {
  background: #e9ecef;
  color: var(--text);
}

/* 共同聊天：外面的按鈕與裡面的面板都要跟著淺色主題切換 */
.light .global-chat-toggle {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .97);
  color: #25282e;
  box-shadow: 0 10px 28px rgba(18, 25, 35, .12);
}

.light .global-chat-toggle:hover {
  background: #f3f4f6;
}

.light .global-chat-toggle b {
  background: #eceff2;
  color: #454a54;
}

.light .global-chat-panel {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .985);
  color: #1f2329;
  box-shadow: 0 24px 70px rgba(18, 25, 35, .16);
}

.light .global-chat-head {
  border-bottom-color: #e5e8ec;
}

.light .global-chat-message {
  border-color: #e1e4e9;
  background: #f6f7f9;
  color: #23262c;
}

.light .global-chat-meta span,
.light .global-chat-empty {
  color: #737983;
  opacity: 1;
}

.light .global-chat-error {
  border-top-color: #e5e8ec;
}

.light .global-chat-input {
  border-top-color: #e5e8ec;
  background: #ffffff;
}

.light .global-chat-input input {
  border-color: #d9dde3;
  background: #f7f8fa;
  color: #1f2329;
}

.light .global-chat-input input::placeholder {
  color: #8a9099;
}

.light .global-chat-input input:focus {
  border-color: #aeb4bd;
  background: #ffffff;
}

.light .global-chat-input button {
  background: #202328;
  color: #ffffff;
}

</style>

<style>
.app::-webkit-scrollbar,
.app *::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}
.app::-webkit-scrollbar-track,
.app *::-webkit-scrollbar-track {
  background: transparent;
}
.app::-webkit-scrollbar-thumb,
.app *::-webkit-scrollbar-thumb {
  background: #555a63;
  border: 2px solid transparent;
  border-radius: 999px;
  background-clip: padding-box;
}
.app.light::-webkit-scrollbar-thumb,
.app.light *::-webkit-scrollbar-thumb {
  background: #b9bec7;
}
.app::-webkit-scrollbar-thumb:hover,
.app *::-webkit-scrollbar-thumb:hover {
  background: #6d737e;
  background-clip: padding-box;
}
.app.light::-webkit-scrollbar-thumb:hover,
.app.light *::-webkit-scrollbar-thumb:hover {
  background: #9fa5ae;
  background-clip: padding-box;
}

/* ===== LIGHT THEME FIXES: controls that were still using dark-mode hardcoded colors ===== */
.light .sidebar-bottom {
  background: var(--panel);
  border-top-color: #e6e8ed;
}

.light .new-chat {
  border-color: #dfe3e7;
  background: #ffffff;
}

.light .new-chat:hover {
  border-color: #cfd4da;
  background: #f3f4f6;
}

.light .new-chat kbd {
  border-color: #d7dbe1;
  background: #f1f3f5;
  color: #656b74;
  box-shadow: inset 0 -1px 0 rgba(20, 24, 30, .05);
}

.light .account {
  border-radius: 10px;
  background: #ffffff;
}

.light .account-avatar {
  border-color: #d9dde3;
  background: #eef0f3;
  color: #30343b;
}

.light .side-tool:hover,
.light .logout:hover {
  background: #f0f1f3;
}

.light .model-button {
  border-color: #dde1e7;
  background: #f3f4f6;
  color: #454a54;
}

.light .model-button:hover {
  border-color: #cfd4da;
  background: #e9ecef;
}

.light .tool-button {
  background: #f3f4f6;
  color: #6f7680;
}

.light .tool-button:hover {
  background: #e9ecef;
  color: var(--text);
}

/* 共同聊天：外面的按鈕與裡面的面板都要跟著淺色主題切換 */
.light .global-chat-toggle {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .97);
  color: #25282e;
  box-shadow: 0 10px 28px rgba(18, 25, 35, .12);
}

.light .global-chat-toggle:hover {
  background: #f3f4f6;
}

.light .global-chat-toggle b {
  background: #eceff2;
  color: #454a54;
}

.light .global-chat-panel {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .985);
  color: #1f2329;
  box-shadow: 0 24px 70px rgba(18, 25, 35, .16);
}

.light .global-chat-head {
  border-bottom-color: #e5e8ec;
}

.light .global-chat-message {
  border-color: #e1e4e9;
  background: #f6f7f9;
  color: #23262c;
}

.light .global-chat-meta span,
.light .global-chat-empty {
  color: #737983;
  opacity: 1;
}

.light .global-chat-error {
  border-top-color: #e5e8ec;
}

.light .global-chat-input {
  border-top-color: #e5e8ec;
  background: #ffffff;
}

.light .global-chat-input input {
  border-color: #d9dde3;
  background: #f7f8fa;
  color: #1f2329;
}

.light .global-chat-input input::placeholder {
  color: #8a9099;
}

.light .global-chat-input input:focus {
  border-color: #aeb4bd;
  background: #ffffff;
}

.light .global-chat-input button {
  background: #202328;
  color: #ffffff;
}

</style>

<style scoped>
/* Enhanced uploads + global chat */
.composer-area.is-dragging {
  border-radius: 22px;
}
.composer-area.is-dragging::before {
  content: '放開即可附加圖片或檔案';
  display: flex;
  align-items: center;
  justify-content: center;
  position: absolute;
  inset: 0;
  z-index: 10;
  border: 2px dashed currentColor;
  border-radius: 22px;
  background: rgba(14, 24, 40, .92);
  backdrop-filter: blur(10px);
  font-weight: 800;
  pointer-events: none;
}
.upload-unified {
  margin-left: 2px;
}
.upload-preview {
  flex-wrap: wrap;
}
.preview-image-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.preview-image-wrap img {
  max-height: 74px;
  max-width: 120px;
  border-radius: 12px;
  object-fit: cover;
}
.preview-image-wrap button,
.pending-file button {
  border: 0;
  cursor: pointer;
}
.preview-image-wrap button {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
}
.pending-file {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  min-width: 180px;
  max-width: 300px;
  padding: 9px 11px;
  border-radius: 12px;
  background: rgba(127, 127, 127, .12);
}
.pending-file > div {
  min-width: 0;
}
.pending-file strong,
.pending-file small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending-file small {
  opacity: .65;
  margin-top: 2px;
}
.pending-file button {
  margin-left: auto;
  background: transparent;
  color: inherit;
  font-size: 18px;
}
.upload-hint {
  width: 100%;
  font-size: 11px;
  opacity: .55;
}
.message-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 10px;
}
.message-file {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 170px;
  max-width: 320px;
  padding: 9px 11px;
  border-radius: 12px;
  text-decoration: none;
  color: inherit;
  background: rgba(127, 127, 127, .10);
}
.message-file:hover {
  background: rgba(127, 127, 127, .18);
}
.file-icon {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: rgba(127, 127, 127, .15);
}
.file-info {
  min-width: 0;
  flex: 1;
}
.file-info strong,
.file-info small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-info small {
  margin-top: 2px;
  opacity: .6;
}
.global-chat {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 120;
}
.global-chat-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 999px;
  padding: 11px 15px;
  background: rgba(10, 17, 29, .94);
  color: inherit;
  box-shadow: 0 14px 40px rgba(0,0,0,.28);
  cursor: pointer;
  backdrop-filter: blur(16px);
}
.global-chat-toggle b {
  min-width: 20px;
  padding: 2px 6px;
  border-radius: 999px;
  font-size: 11px;
  background: rgba(255,255,255,.12);
}
.global-chat-panel {
  position: absolute;
  right: 0;
  bottom: 54px;
  width: min(370px, calc(100vw - 28px));
  height: 480px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 18px;
  background: rgba(7, 13, 24, .97);
  box-shadow: 0 24px 70px rgba(0,0,0,.4);
  backdrop-filter: blur(18px);
}
.global-chat-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 15px 16px;
  border-bottom: 1px solid rgba(255,255,255,.08);
}
.global-chat-head strong,
.global-chat-head small {
  display: block;
}
.global-chat-head small {
  margin-top: 3px;
  opacity: .55;
}
.global-chat-head button {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 22px;
}
.global-chat-list {
  flex: 1;
  overflow: auto;
  padding: 14px;
}
.global-chat-message {
  padding: 9px 10px;
  margin-bottom: 8px;
  border-radius: 12px;
  background: rgba(255,255,255,.045);
}
.global-chat-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 12px;
}
.global-chat-meta span {
  opacity: .48;
  font-size: 10px;
}
.global-chat-text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.45;
}
.global-chat-empty {
  padding: 30px 12px;
  text-align: center;
  opacity: .55;
}
.global-chat-error {
  padding: 7px 11px;
  font-size: 11px;
  color: #ff9d9d;
  border-top: 1px solid rgba(255,255,255,.07);
}
.global-chat-input {
  display: flex;
  gap: 8px;
  padding: 10px;
  border-top: 1px solid rgba(255,255,255,.08);
}
.global-chat-input input {
  flex: 1;
  min-width: 0;
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 10px;
  padding: 10px 11px;
  background: rgba(255,255,255,.05);
  color: inherit;
}
.global-chat-input button {
  width: 42px;
  border: 0;
  border-radius: 10px;
  cursor: pointer;
}
.global-chat-input button:disabled {
  opacity: .45;
  cursor: not-allowed;
}
@media (max-width: 720px), (pointer: coarse) {
  .composer-area {
    position: relative;
    z-index: 130;
    padding-bottom: max(13px, env(safe-area-inset-bottom));
  }

  .global-chat {
    right: 12px;
    /* 避開手機版模型選單與送出按鈕。 */
    bottom: calc(112px + env(safe-area-inset-bottom));
  }

  .global-chat-toggle {
    min-height: 44px;
    padding: 10px 13px;
  }

  .global-chat-panel {
    position: fixed;
    left: 8px;
    right: 8px;
    top: calc(68px + env(safe-area-inset-top));
    bottom: calc(112px + env(safe-area-inset-bottom));
    width: auto;
    height: auto;
    max-height: none;
  }

  .model-popover {
    max-height: calc(100dvh - 150px - env(safe-area-inset-bottom));
    overflow-y: auto;
    overscroll-behavior: contain;
  }
}

@media (max-width: 420px) {
  .composer-area {
    padding-right: 8px;
    padding-left: 8px;
  }

  .model-button {
    max-width: 116px;
  }

  .model-button span:nth-child(2) {
    max-width: 68px;
  }

  .global-chat {
    bottom: calc(120px + env(safe-area-inset-bottom));
  }

  .global-chat-panel {
    bottom: calc(120px + env(safe-area-inset-bottom));
  }
}

/* ===== LIGHT THEME FIXES: controls that were still using dark-mode hardcoded colors ===== */
.light .sidebar-bottom {
  background: var(--panel);
  border-top-color: #e6e8ed;
}

.light .new-chat {
  border-color: #dfe3e7;
  background: #ffffff;
}

.light .new-chat:hover {
  border-color: #cfd4da;
  background: #f3f4f6;
}

.light .new-chat kbd {
  border-color: #d7dbe1;
  background: #f1f3f5;
  color: #656b74;
  box-shadow: inset 0 -1px 0 rgba(20, 24, 30, .05);
}

.light .account {
  border-radius: 10px;
  background: #ffffff;
}

.light .account-avatar {
  border-color: #d9dde3;
  background: #eef0f3;
  color: #30343b;
}

.light .side-tool:hover,
.light .logout:hover {
  background: #f0f1f3;
}

.light .model-button {
  border-color: #dde1e7;
  background: #f3f4f6;
  color: #454a54;
}

.light .model-button:hover {
  border-color: #cfd4da;
  background: #e9ecef;
}

.light .tool-button {
  background: #f3f4f6;
  color: #6f7680;
}

.light .tool-button:hover {
  background: #e9ecef;
  color: var(--text);
}

/* 共同聊天：外面的按鈕與裡面的面板都要跟著淺色主題切換 */
.light .global-chat-toggle {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .97);
  color: #25282e;
  box-shadow: 0 10px 28px rgba(18, 25, 35, .12);
}

.light .global-chat-toggle:hover {
  background: #f3f4f6;
}

.light .global-chat-toggle b {
  background: #eceff2;
  color: #454a54;
}

.light .global-chat-panel {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .985);
  color: #1f2329;
  box-shadow: 0 24px 70px rgba(18, 25, 35, .16);
}

.light .global-chat-head {
  border-bottom-color: #e5e8ec;
}

.light .global-chat-message {
  border-color: #e1e4e9;
  background: #f6f7f9;
  color: #23262c;
}

.light .global-chat-meta span,
.light .global-chat-empty {
  color: #737983;
  opacity: 1;
}

.light .global-chat-error {
  border-top-color: #e5e8ec;
}

.light .global-chat-input {
  border-top-color: #e5e8ec;
  background: #ffffff;
}

.light .global-chat-input input {
  border-color: #d9dde3;
  background: #f7f8fa;
  color: #1f2329;
}

.light .global-chat-input input::placeholder {
  color: #8a9099;
}

.light .global-chat-input input:focus {
  border-color: #aeb4bd;
  background: #ffffff;
}

.light .global-chat-input button {
  background: #202328;
  color: #ffffff;
}

</style>

<!-- 前台質感強化：保留低彩度灰階，僅使用少量綠色提示狀態。 -->
<style scoped>
.app {
  --surface-raised: #28282b;
  --surface-hover: #303034;
  --hairline: rgba(255, 255, 255, .075);
  --hairline-strong: rgba(255, 255, 255, .12);
  background: #202123;
}

.sidebar {
  border-right-color: var(--hairline);
  box-shadow: inset -1px 0 rgba(0, 0, 0, .16);
}

.sidebar-brand {
  position: relative;
  margin-bottom: 12px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--hairline);
}

.brand-logo {
  position: relative;
  border: 1px solid #45454a;
  border-radius: 10px;
  background: #303033;
  color: #f2f2f4;
  box-shadow: none;
}

.brand-logo::after {
  content: '';
  position: absolute;
  right: -3px;
  bottom: -3px;
  width: 9px;
  height: 9px;
  border: 2px solid var(--panel);
  border-radius: 50%;
  background: var(--accent);
}

.sidebar-brand strong {
  letter-spacing: .01em;
}

.sidebar-brand small {
  margin-top: 3px;
  color: #85858d;
  letter-spacing: .025em;
}

.new-chat {
  border-color: #424246;
  border-radius: 10px;
  background: #252527;
  transition: border-color .16s ease, background .16s ease;
}

.new-chat:hover {
  border-color: #535359;
  background: #2d2d30;
}

.new-chat kbd {
  border-color: #48484d;
  background: #303033;
}

.conversation-search {
  border-color: transparent;
  border-radius: 9px;
  background: #222224;
  transition: border-color .16s ease, background .16s ease;
}

.conversation-search:focus-within {
  border-color: #505056;
  background: #28282b;
}

.history-head {
  letter-spacing: .08em;
  text-transform: none;
}

.conversation-row {
  position: relative;
  overflow: hidden;
  border-radius: 8px;
}

.conversation-row.active {
  background: #2b2b2e;
}

.conversation-row.active::before {
  content: '';
  position: absolute;
  top: 9px;
  bottom: 9px;
  left: 0;
  width: 2px;
  border-radius: 2px;
  background: #a9a9af;
}

.conversation-row:hover:not(.active) {
  background: rgba(255, 255, 255, .035);
}

.conversation-menu {
  transition: opacity .15s ease, background .15s ease;
}

.usage-card {
  border-color: var(--hairline);
  border-radius: 11px;
  background: #202022;
  transition: border-color .16s ease, background .16s ease;
}

.usage-card:hover {
  border-color: var(--hairline-strong);
  background: #252527;
}

.usage-bar {
  height: 4px;
  background: #3a3a3e;
}

.usage-bar span {
  background: #9c9ca3;
}

.sidebar-bottom {
  background: #181818;
}

.side-tool,
.logout,
.icon-button,
.mobile-button {
  transition: background .15s ease, color .15s ease, border-color .15s ease;
}

.account-avatar {
  border: 1px solid #48484d;
  background: #333337;
}

.topbar {
  border-bottom-color: var(--hairline);
  background: rgba(33, 33, 35, .91);
  box-shadow: 0 1px 12px rgba(0, 0, 0, .08);
}

.chat-title strong {
  letter-spacing: -.012em;
}

.chat-title span {
  position: relative;
  padding-left: 11px;
}

.chat-title span::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #77777f;
  transform: translateY(-50%);
}

.icon-button {
  border: 1px solid transparent;
}

.icon-button:hover {
  border-color: var(--hairline);
  background: #2d2d30;
}

.chat-scroll {
  background: #212224;
}

.welcome {
  position: relative;
}

.welcome-mark {
  position: relative;
  width: 62px;
  height: 62px;
  border: 1px solid #46464b;
  border-radius: 17px;
  background: #2d2d30;
  color: #f0f0f2;
  box-shadow: 0 16px 42px rgba(0, 0, 0, .2);
}

.welcome-mark::after {
  content: '';
  position: absolute;
  right: 7px;
  bottom: 7px;
  width: 7px;
  height: 7px;
  border: 2px solid #2d2d30;
  border-radius: 50%;
  background: var(--accent);
}

.welcome .eyebrow {
  letter-spacing: .1em;
}

.welcome h1 {
  margin-top: 10px;
  letter-spacing: -.035em;
}

.welcome-features {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 7px;
  margin-top: 16px;
}

.welcome-features span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 29px;
  padding: 0 10px;
  border: 1px solid var(--hairline);
  border-radius: 8px;
  background: rgba(255, 255, 255, .018);
  color: #a5a5ac;
  font-size: 10px;
}

.welcome-features i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #77777f;
}

.suggestions {
  gap: 10px;
  margin-top: 18px;
}

.suggestions button {
  position: relative;
  overflow: hidden;
  border-color: var(--hairline);
  border-radius: 11px;
  background: #262628;
  box-shadow: 0 5px 16px rgba(0, 0, 0, .08);
}

.suggestions button:hover {
  border-color: var(--hairline-strong);
  background: #2c2c2f;
  transform: translateY(-1px);
}

.suggestion-icon {
  border: 1px solid #424247;
  border-radius: 9px;
  background: #303034;
  color: #d0d0d5;
}

.message {
  border-color: var(--hairline) !important;
}

.message-main {
  min-width: 0;
}

.avatar.user {
  border: 1px solid #4a4a4f;
  background: #37373b;
}

.avatar.assistant {
  border: 1px solid rgba(255, 255, 255, .12);
  background: #168b70;
  box-shadow: 0 5px 14px rgba(0, 0, 0, .12);
}

.model-badge {
  border: 1px solid var(--hairline);
  background: #2b2b2e;
  color: #9c9ca4;
}

.message-tools button {
  border-radius: 7px;
}

.composer-area {
  background: #212224;
}

.composer {
  border-color: #48484d;
  border-radius: 16px;
  background: #2b2b2e;
  box-shadow: 0 12px 32px rgba(0, 0, 0, .18);
  transition: border-color .16s ease, box-shadow .16s ease;
}

.composer:focus-within {
  border-color: #66666d;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, .035), 0 14px 34px rgba(0, 0, 0, .2);
}

.tool-button,
.model-button {
  border-color: transparent;
  background: #303034;
}

.tool-button:hover,
.model-button:hover {
  background: #39393d;
}

.model-dot {
  background: var(--accent);
  box-shadow: none;
}

.send-button {
  border: 1px solid #eeeef1;
  border-radius: 10px;
  box-shadow: none;
  transition: transform .14s ease, opacity .14s ease;
}

.send-button:not(:disabled):active {
  transform: scale(.95);
}

.model-popover,
.share-dialog,
.usage-dialog {
  border-color: #46464b;
  border-radius: 13px;
  background: #262628;
  box-shadow: 0 24px 70px rgba(0, 0, 0, .35);
}

.global-chat-toggle {
  border-color: #4a4a4f;
  border-radius: 11px;
  background: rgba(43, 43, 46, .96);
  box-shadow: 0 12px 32px rgba(0, 0, 0, .24);
}

.global-chat-toggle:hover {
  background: #343438;
}

.global-chat-panel {
  border-color: #48484d;
  border-radius: 14px;
  background: rgba(38, 38, 40, .98);
  box-shadow: 0 24px 70px rgba(0, 0, 0, .38);
}

.global-chat-message {
  border: 1px solid rgba(255, 255, 255, .045);
  border-radius: 9px;
  background: #2d2d30;
}

.global-chat-input input {
  border-color: #48484d;
  border-radius: 9px;
  background: #212123;
}

.global-chat-input input:focus {
  outline: none;
  border-color: #68686f;
}

.scroll-bottom {
  border-color: #4b4b50;
  background: #303034;
}

.light {
  --surface-raised: #fff;
  --surface-hover: #f0f1f3;
  --hairline: rgba(20, 24, 30, .09);
  --hairline-strong: rgba(20, 24, 30, .15);
}

.light .brand-logo,
.light .welcome-mark {
  border-color: #d8dbe0;
  background: #fff;
  color: #24262b;
}

.light .brand-logo::after {
  border-color: var(--panel);
}

.light .welcome-mark::after {
  border-color: #fff;
}

.light .new-chat,
.light .conversation-search,
.light .suggestions button,
.light .composer,
.light .global-chat-message {
  border-color: #dfe2e7;
  background: #fff;
}

.light .chat-scroll,
.light .composer-area {
  background: #f7f8fa;
}

.light .conversation-row.active {
  background: #eceef1;
}

.light .topbar {
  background: rgba(247, 248, 250, .91);
}

@media (max-width: 620px), (pointer: coarse) {
  .welcome-features {
    gap: 5px;
    margin-top: 13px;
  }

  .welcome-features span {
    min-height: 27px;
    padding: 0 8px;
    font-size: 9px;
  }

  .suggestions button {
    min-height: 68px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app *,
  .app *::before,
  .app *::after {
    scroll-behavior: auto !important;
    animation-duration: .01ms !important;
    transition-duration: .01ms !important;
  }
}

/* ===== LIGHT THEME FIXES: controls that were still using dark-mode hardcoded colors ===== */
.light .sidebar-bottom {
  background: var(--panel);
  border-top-color: #e6e8ed;
}

.light .new-chat {
  border-color: #dfe3e7;
  background: #ffffff;
}

.light .new-chat:hover {
  border-color: #cfd4da;
  background: #f3f4f6;
}

.light .new-chat kbd {
  border-color: #d7dbe1;
  background: #f1f3f5;
  color: #656b74;
  box-shadow: inset 0 -1px 0 rgba(20, 24, 30, .05);
}

.light .account {
  border-radius: 10px;
  background: #ffffff;
}

.light .account-avatar {
  border-color: #d9dde3;
  background: #eef0f3;
  color: #30343b;
}

.light .side-tool:hover,
.light .logout:hover {
  background: #f0f1f3;
}

.light .model-button {
  border-color: #dde1e7;
  background: #f3f4f6;
  color: #454a54;
}

.light .model-button:hover {
  border-color: #cfd4da;
  background: #e9ecef;
}

.light .tool-button {
  background: #f3f4f6;
  color: #6f7680;
}

.light .tool-button:hover {
  background: #e9ecef;
  color: var(--text);
}

/* 共同聊天：外面的按鈕與裡面的面板都要跟著淺色主題切換 */
.light .global-chat-toggle {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .97);
  color: #25282e;
  box-shadow: 0 10px 28px rgba(18, 25, 35, .12);
}

.light .global-chat-toggle:hover {
  background: #f3f4f6;
}

.light .global-chat-toggle b {
  background: #eceff2;
  color: #454a54;
}

.light .global-chat-panel {
  border-color: #d9dde3;
  background: rgba(255, 255, 255, .985);
  color: #1f2329;
  box-shadow: 0 24px 70px rgba(18, 25, 35, .16);
}

.light .global-chat-head {
  border-bottom-color: #e5e8ec;
}

.light .global-chat-message {
  border-color: #e1e4e9;
  background: #f6f7f9;
  color: #23262c;
}

.light .global-chat-meta span,
.light .global-chat-empty {
  color: #737983;
  opacity: 1;
}

.light .global-chat-error {
  border-top-color: #e5e8ec;
}

.light .global-chat-input {
  border-top-color: #e5e8ec;
  background: #ffffff;
}

.light .global-chat-input input {
  border-color: #d9dde3;
  background: #f7f8fa;
  color: #1f2329;
}

.light .global-chat-input input::placeholder {
  color: #8a9099;
}

.light .global-chat-input input:focus {
  border-color: #aeb4bd;
  background: #ffffff;
}

.light .global-chat-input button {
  background: #202328;
  color: #ffffff;
}

</style>
