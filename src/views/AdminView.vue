<script setup>
import {
  ref,
  shallowRef,
  computed,
  onMounted,
  onUnmounted,
  nextTick
} from 'vue'

/* =========================
   基本狀態
========================= */

const loggedIn = ref(false)
const adminChecking = ref(true)
const loading = ref(false)

const loginUsername = ref('')
const loginPassword = ref('')
const loginError = ref('')

const adminName = ref('Admin')

// 日期只在進入頁面時產生一次，不需要做成響應式資料。
const currentDateLabel = new Intl.DateTimeFormat('zh-TW', {
  month: 'long',
  day: 'numeric',
  weekday: 'short'
}).format(new Date())

const currentPage = ref('users')
const sidebarOpen = ref(false)

// 大型 API 結果只會整批替換，不需要把每個巢狀欄位都轉成 Proxy。
const users = shallowRef([])
const searchInput = ref('')
const search = ref('')
let searchTimer = null

/* =========================
   Dashboard 統計
========================= */

const summary = ref({
  users: 0,
  conversations: 0,
  messages: 0,

  total_tokens: 0,
  input_tokens: 0,
  output_tokens: 0,

  reasoning_tokens: 0,
  reasoning_cost: 0,

  api_requests: 0,
  total_cost: 0,

  month_tokens: 0,
  month_reasoning_tokens: 0,
  month_reasoning_cost: 0,
  month_requests: 0,
  month_cost: 0
})

const models = shallowRef([])
const dailyUsage = shallowRef([])

const webSearchCalls = ref(0)
const webSearchCost = ref(0)

/*
 * month_cost = API + Web Search
 *
 * reasoning_cost 是 API output token 成本其中的一部分，
 * 不能再次加進總金額。
 */
const monthConsumption = computed(() =>
  Number(summary.value.month_cost || 0)
)

const monthApiCost = computed(() => {
  const total =
    Number(summary.value.month_cost || 0)

  const searchCost =
    Number(webSearchCost.value || 0)

  return Math.max(
    0,
    total - searchCost
  )
})

/* =========================
   Traffic
========================= */

const traffic = shallowRef({
  summary: {},
  today: {},
  daily: [],
  pages: [],
  sources: [],
  devices: [],
  browsers: [],
  systems: [],
  recent: []
})

/* =========================
   Chart
========================= */

const usageCanvas = ref(null)
const trafficCanvas = ref(null)

let usageChart = null
let trafficChart = null
let ChartClass = null
let chartLoader = null

async function ensureChartJs() {
  if (ChartClass) return ChartClass
  if (!chartLoader) {
    chartLoader = import('chart.js').then(module => {
      const {
        Chart,
        BarController,
        LineController,
        BarElement,
        LineElement,
        PointElement,
        CategoryScale,
        LinearScale,
        Legend,
        Tooltip,
        Filler
      } = module

      Chart.register(
        BarController,
        LineController,
        BarElement,
        LineElement,
        PointElement,
        CategoryScale,
        LinearScale,
        Legend,
        Tooltip,
        Filler
      )
      ChartClass = Chart
      return Chart
    })
  }
  return chartLoader
}

/* =========================
   使用者 Modal
========================= */

const userModal = ref(false)
const editingUser = shallowRef(null)

const form = ref({
  username: '',
  password: '',
  display_name: '',
  email: ''
})

/* =========================
   Toast
========================= */

const toastText = ref('')
const toastVisible = ref(false)

function fmt(value) {
  return Number(
    value || 0
  ).toLocaleString('zh-TW')
}

function money(value) {
  return '$' +
    Number(
      value || 0
    ).toFixed(4)
}

function showToast(message) {
  toastText.value = message
  toastVisible.value = true

  clearTimeout(
    showToast.timer
  )

  showToast.timer =
    setTimeout(() => {
      toastVisible.value = false
    }, 2200)
}

/* =========================
   API helper
========================= */

async function api(
  url,
  options = {}
) {
  const response =
    await fetch(
      url,
      {
        credentials: 'include',
        ...options
      }
    )

  const contentType =
    response.headers.get(
      'content-type'
    ) || ''

  let data

  if (
    contentType.includes(
      'application/json'
    )
  ) {
    data =
      await response.json()
  } else {
    data =
      await response.text()
  }

  if (!response.ok) {
    throw new Error(
      typeof data === 'object'
        ? data.error ||
          `HTTP ${response.status}`
        : data ||
          `HTTP ${response.status}`
    )
  }

  return data
}

/* =========================
   LOGIN
========================= */

async function checkAdmin() {
  try {
    const sessionResult =
      await api(
        '/api/admin/check'
      )

    if (
      !sessionResult?.logged_in
    ) {
      loggedIn.value = false

      sessionStorage.removeItem(
        'ggpt_admin_session'
      )

      return
    }

    loggedIn.value = true

    sessionStorage.setItem(
      'ggpt_admin_session',
      '1'
    )

    adminName.value =
      sessionResult.username ||
      'Admin'

    await loadDashboard()
  } catch (error) {
    console.error(
      'Admin session check failed:',
      error
    )

    loggedIn.value = false

    sessionStorage.removeItem(
      'ggpt_admin_session'
    )
  } finally {
    adminChecking.value =
      false
  }
}

async function login() {
  loginError.value = ''

  if (
    !loginUsername.value ||
    !loginPassword.value
  ) {
    loginError.value =
      '請輸入管理員帳號與密碼'

    return
  }

  loading.value = true

  try {
    const data =
      await api(
        '/api/admin/login',
        {
          method: 'POST',

          headers: {
            'Content-Type':
              'application/json'
          },

          body:
            JSON.stringify({
              username:
                loginUsername.value,

              password:
                loginPassword.value
            })
        }
      )

    loggedIn.value = true

    sessionStorage.setItem(
      'ggpt_admin_session',
      '1'
    )

    adminName.value =
      data.username ||
      'Admin'

    loginPassword.value = ''

    await loadDashboard()
  } catch (error) {
    loginError.value =
      error.message
  } finally {
    loading.value = false
  }
}

async function logout() {
  try {
    await api(
      '/api/admin/logout',
      {
        method: 'POST'
      }
    )
  } catch {
    // ignore
  }

  loggedIn.value = false

  sessionStorage.removeItem(
    'ggpt_admin_session'
  )
}

/* =========================
   NAV
========================= */

async function changePage(
  page
) {
  currentPage.value = page
  sidebarOpen.value = false

  if (
    page === 'users'
  ) {
    await loadDashboard()
  } else {
    await loadTraffic()
  }
}

async function refreshCurrentPage() {
  try {
    if (
      currentPage.value ===
      'users'
    ) {
      await loadDashboard()
    } else {
      await loadTraffic()
    }

    showToast(
      '資料已更新'
    )
  } catch (error) {
    showToast(
      error.message
    )
  }
}

/* =========================
   DASHBOARD
========================= */

function applyDashboardData(
  data
) {
  /*
   * 保留預設欄位，
   * 避免後端某個欄位暫時不存在時變 undefined
   */
  summary.value = {
    ...summary.value,
    ...(data?.summary || {})
  }

  users.value =
    data?.users || []
  userPage.value = 1

  models.value =
    data?.models || []

  dailyUsage.value =
    data?.daily || []

  webSearchCalls.value =
    Number(
      data?.web_search_calls ||
      0
    )

  webSearchCost.value =
    Number(
      data?.web_search_cost ||
      0
    )
}

async function loadDashboard(
  dataOverride = null
) {
  try {
    const data =
      dataOverride ||
      await api(
        '/api/admin/dashboard'
      )

    applyDashboardData(
      data
    )

    await nextTick()

    requestAnimationFrame(() => {
      void renderUsageChart()
    })
  } catch (error) {
    showToast(
      `讀取管理統計失敗：${error.message}`
    )
  }
}

/* =========================
   TRAFFIC
========================= */

async function loadTraffic() {
  try {
    const data =
      await api(
        '/api/admin/traffic'
      )

    traffic.value = {
      summary:
        data.summary || {},

      today:
        data.today || {},

      daily:
        data.daily || [],

      pages:
        data.pages || [],

      sources:
        data.sources || [],

      devices:
        data.devices || [],

      browsers:
        data.browsers || [],

      systems:
        data.systems || [],

      recent:
        data.recent || []
    }

    await nextTick()

    requestAnimationFrame(() => {
      void renderTrafficChart()
    })
  } catch (error) {
    showToast(
      `讀取訪客流量失敗：${error.message}`
    )
  }
}

/* =========================
   TOKEN CHART
========================= */

async function renderUsageChart() {
  const canvas = usageCanvas.value

  if (!canvas) {
    console.warn('模型金額圖表 canvas 尚未建立')
    return
  }

  const Chart = await ensureChartJs()
  const rows = Array.isArray(models.value)
    ? models.value
        .map(item => ({
          model: String(item.model || 'Unknown'),
          total_cost: Number(item.total_cost || 0)
        }))
        .sort((a, b) => b.total_cost - a.total_cost)
    : []

  // 清除同一個 canvas 上可能殘留的 Chart.js 實例。
  const existingChart = Chart.getChart(canvas)
  if (existingChart) {
    existingChart.destroy()
  }

  if (usageChart) {
    try {
      usageChart.destroy()
    } catch (_) {
      // 已被 Chart.getChart(canvas) 清除時可忽略。
    }
    usageChart = null
  }

  if (!rows.length) {
    console.warn('沒有模型金額資料可顯示')
    return
  }

  usageChart = new Chart(canvas, {
    type: 'bar',

    data: {
      labels: rows.map(item => item.model),

      datasets: [
        {
          label: '總金額',
          data: rows.map(item => item.total_cost),
          backgroundColor: 'rgba(154,154,160,.72)',
          borderColor: 'rgba(190,190,196,1)',
          borderWidth: 1,
          borderRadius: 7
        }
      ]
    },

    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false
      },

      plugins: {
        legend: {
          display: false
        },

        tooltip: {
          callbacks: {
            label(context) {
              return `總金額：$${Number(context.raw || 0).toFixed(6)}`
            }
          }
        }
      },

      scales: {
        x: {
          ticks: {
            color: '#91919a',
            maxRotation: 35,
            minRotation: 0,
            autoSkip: false
          },
          grid: {
            color: 'rgba(255,255,255,.04)'
          }
        },

        y: {
          beginAtZero: true,
          ticks: {
            color: '#91919a',
            callback(value) {
              return '$' + Number(value || 0).toFixed(4)
            }
          },
          grid: {
            color: 'rgba(255,255,255,.05)'
          }
        }
      }
    }
  })
}

/* =========================
   TRAFFIC CHART
========================= */

async function renderTrafficChart() {
  if (
    !trafficCanvas.value
  ) {
    return
  }

  const Chart = await ensureChartJs()

  if (trafficChart) {
    trafficChart.destroy()
  }

  const data =
    traffic.value.daily

  trafficChart =
    new Chart(
      trafficCanvas.value,
      {
        type: 'line',

        data: {
          labels:
            data.map(
              item =>
                item.day
            ),

          datasets: [
            {
              label:
                '瀏覽量',

              data:
                data.map(
                  item =>
                    Number(
                      item.page_views ||
                      0
                    )
                ),

              borderColor:
                'rgba(184,184,190,1)',

              backgroundColor:
                'rgba(184,184,190,.08)',

              fill:
                true,

              tension:
                .25,

              pointRadius:
                2
            },

            {
              label:
                '不重複訪客',

              data:
                data.map(
                  item =>
                    Number(
                      item.unique_visitors ||
                      0
                    )
                ),

              borderColor:
                'rgba(99,219,184,1)',

              backgroundColor:
                'rgba(99,219,184,.07)',

              fill:
                true,

              tension:
                .25,

              pointRadius:
                2
            }
          ]
        },

        options: {
          responsive: true,

          maintainAspectRatio:
            false,

          interaction: {
            mode:
              'index',

            intersect:
              false
          },

          plugins: {
            legend: {
              labels: {
                color:
                  '#aaaab1'
              }
            }
          },

          scales: {
            x: {
              ticks: {
                color:
                  '#91919a',

                maxRotation:
                  0,

                autoSkip:
                  true,

                maxTicksLimit:
                  10
              },

              grid: {
                color:
                  'rgba(255,255,255,.04)'
              }
            },

            y: {
              beginAtZero:
                true,

              ticks: {
                color:
                  '#91919a'
              },

              grid: {
                color:
                  'rgba(255,255,255,.05)'
              }
            }
          }
        }
      }
    )
}

/* =========================
   USERS
========================= */

const filteredUsers =
  computed(() => {
    const q =
      search.value
        .trim()
        .toLowerCase()

    if (!q) {
      return users.value
    }

    return users.value.filter(
      user =>
        [
          user.username,
          user.display_name,
          user.email
        ].some(
          value =>
            String(
              value || ''
            )
              .toLowerCase()
              .includes(q)
        )
    )
  })

function openNewUser() {
  editingUser.value = null

  form.value = {
    username: '',
    password: '',
    display_name: '',
    email: ''
  }

  userModal.value = true
}

function openEditUser(
  user
) {
  editingUser.value =
    user

  form.value = {
    username:
      user.username || '',

    password: '',

    display_name:
      user.display_name ||
      '',

    email:
      user.email || ''
  }

  userModal.value =
    true
}

function closeUserModal() {
  userModal.value =
    false
}

async function saveUser() {
  loading.value = true

  try {
    const normalizedUsername = form.value.username.normalize('NFKC').trim()
    if (normalizedUsername.length < 3 || normalizedUsername.length > 32) {
      throw new Error('帳號長度需為 3～32 個字元')
    }
    if ((!editingUser.value || form.value.password) &&
        (form.value.password.length < 8 || form.value.password.length > 128)) {
      throw new Error('密碼長度需為 8～128 個字元')
    }
    form.value.username = normalizedUsername

    if (
      editingUser.value
    ) {
      await api(
        `/api/admin/users/${editingUser.value.id}`,
        {
          method: 'PATCH',

          headers: {
            'Content-Type':
              'application/json'
          },

          body:
            JSON.stringify({
              username:
                form.value.username,

              password:
                form.value.password,

              display_name:
                form.value.display_name,

              email:
                form.value.email
            })
        }
      )

      showToast(
        '使用者已更新'
      )
    } else {
      await api(
        '/api/admin/users',
        {
          method: 'POST',

          headers: {
            'Content-Type':
              'application/json'
          },

          body:
            JSON.stringify({
              username:
                form.value.username,

              password:
                form.value.password,

              display_name:
                form.value.display_name,

              email:
                form.value.email
            })
        }
      )

      showToast(
        '使用者已新增'
      )
    }

    closeUserModal()

    await loadDashboard()
  } catch (error) {
    showToast(
      error.message
    )
  } finally {
    loading.value = false
  }
}

async function deleteUser(
  user
) {
  if (
    !window.confirm(
      `確定刪除「${user.username}」？\n\n會一併刪除對話、訊息與 Token 使用紀錄，且無法復原。`
    )
  ) {
    return
  }

  try {
    await api(
      `/api/admin/users/${user.id}`,
      {
        method:
          'DELETE'
      }
    )

    showToast(
      '使用者已刪除'
    )

    await loadDashboard()
  } catch (error) {
    showToast(
      error.message
    )
  }
}

/* =========================
   DATE
========================= */

function formatDate(
  value
) {
  try {
    return new Date(
      value
    ).toLocaleString(
      'zh-TW',
      {
        hour12: false
      }
    )
  } catch {
    return String(
      value || ''
    )
  }
}

/* =========================
   SORT
========================= */

const userSortKey =
  ref('total_tokens')

const userSortDir =
  ref('desc')

const userSearchFocus =
  ref(false)

const expandedSections =
  ref({
    usage: true,
    models: true,
    users: true
  })

const sortedFilteredUsers =
  computed(() => {
    const rows =
      [...filteredUsers.value]

    const key =
      userSortKey.value

    const dir =
      userSortDir.value ===
      'asc'
        ? 1
        : -1

    rows.sort(
      (a, b) => {
        const av =
          a?.[key]

        const bv =
          b?.[key]

        if (
          key ===
            'display_name' ||
          key ===
            'username' ||
          key ===
            'email' ||
          key ===
            'auth_provider'
        ) {
          return (
            String(
              av || ''
            ).localeCompare(
              String(
                bv || ''
              ),
              'zh-Hant'
            ) * dir
          )
        }

        return (
          Number(
            av || 0
          ) -
          Number(
            bv || 0
          )
        ) * dir
      }
    )

    return rows
  })

const USER_PAGE_SIZE = 25
const userPage = ref(1)

const userPageCount = computed(() =>
  Math.max(1, Math.ceil(sortedFilteredUsers.value.length / USER_PAGE_SIZE))
)

const paginatedUsers = computed(() => {
  const page = Math.min(userPage.value, userPageCount.value)
  const start = (page - 1) * USER_PAGE_SIZE
  return sortedFilteredUsers.value.slice(start, start + USER_PAGE_SIZE)
})

const maxModelTokens = computed(() =>
  Math.max(1, ...models.value.map(item => Number(item.total_tokens || 0)))
)

function handleUserSearchInput(event) {
  const value = event.target.value
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    search.value = value
    userPage.value = 1
  }, 250)
}

function clearUserSearch() {
  clearTimeout(searchTimer)
  searchInput.value = ''
  search.value = ''
  userPage.value = 1
}

function changeUserPage(page) {
  userPage.value = Math.min(Math.max(1, page), userPageCount.value)
}

function toggleUserSort(
  key
) {
  if (
    userSortKey.value ===
    key
  ) {
    userSortDir.value =
      userSortDir.value ===
      'asc'
        ? 'desc'
        : 'asc'
  } else {
    userSortKey.value =
      key

    userSortDir.value =
      'desc'
  }

  userPage.value = 1
}

function sortIndicator(
  key
) {
  if (
    userSortKey.value !==
    key
  ) {
    return ''
  }

  return (
    userSortDir.value ===
    'asc'
      ? ' ↑'
      : ' ↓'
  )
}

/* =========================
   CSV
========================= */

function exportUsersCsv() {
  if (
    !users.value.length
  ) {
    showToast(
      '目前沒有使用者資料可匯出'
    )

    return
  }

  const headers = [
    'ID',
    '帳號',
    '顯示名稱',
    'Email',
    '登入方式',
    '對話數',
    '訊息數',
    'API請求',
    'Token',
    '思考Token',
    '思考金額',
    'API金額',
    'Web Search次數',
    'Web Search金額',
    '總金額'
  ]

  const rows =
    users.value.map(
      user => [
        user.id,
        user.username,
        user.display_name ||
          '',
        user.email ||
          '',
        user.auth_provider ||
          'local',

        user.conversation_count ||
          0,

        user.message_count ||
          0,

        user.api_requests ||
          0,

        user.total_tokens ||
          0,

        user.reasoning_tokens ||
          0,

        user.reasoning_cost ||
          0,

        Math.max(
          0,
          Number(
            user.total_cost ||
            0
          ) -
          Number(
            user.web_search_cost ||
            0
          )
        ),

        user.web_search_calls ||
          0,

        user.web_search_cost ||
          0,

        user.total_cost ||
          0
      ]
    )

  const csv =
    [
      headers,
      ...rows
    ]
      .map(
        row =>
          row.map(
            value => {
              const text =
                String(
                  value ??
                  ''
                )

              return `"${text.replaceAll(
                '"',
                '""'
              )}"`
            }
          ).join(',')
      )
      .join('\r\n')

  const blob =
    new Blob(
      [
        '\uFEFF' +
        csv
      ],
      {
        type:
          'text/csv;charset=utf-8;'
      }
    )

  const url =
    URL.createObjectURL(
      blob
    )

  const link =
    document.createElement(
      'a'
    )

  link.href =
    url

  link.download =
    `ggpt-users-${new Date()
      .toISOString()
      .slice(
        0,
        10
      )}.csv`

  document.body.appendChild(
    link
  )

  link.click()

  link.remove()

  URL.revokeObjectURL(
    url
  )

  showToast(
    '使用者資料已匯出 CSV'
  )
}

function toggleSection(
  name
) {
  expandedSections.value[
    name
  ] =
    !expandedSections.value[
      name
    ]
}

onMounted(() => {
  void checkAdmin()
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  usageChart?.destroy()
  trafficChart?.destroy()
})
</script>

<template>
  <!-- ========================
       驗證管理員
  ========================= -->

  <div
    v-if="
      adminChecking &&
      !loggedIn
    "
    class="admin-login admin-boot"
  >
    <div class="login-box boot-box">
      <div class="brand-mark admin-mark">
        ▦
      </div>

      <div class="eyebrow">
        GGPT 管理後台
      </div>

      <h1>
        正在進入控制中心
      </h1>

      <p>
        正在驗證管理員工作階段…
      </p>

      <div class="boot-spinner"></div>
    </div>
  </div>

  <!-- ========================
       登入
  ========================= -->

  <div
    v-else-if="
      !loggedIn
    "
    class="admin-login"
  >
    <div class="login-shell">
      <div class="login-glow glow-a"></div>
      <div class="login-glow glow-b"></div>

      <div class="login-box">
        <div class="brand-mark admin-mark">
          ▦
        </div>

        <div class="eyebrow">
          GGPT 管理後台
        </div>

        <h1>
          管理員登入
        </h1>

        <p>
          集中查看使用量、訪客流量與會員資料。
        </p>

        <form
          @submit.prevent="
            login
          "
          class="login-form"
        >
          <label
            for="admin-username"
          >
            管理員帳號
          </label>

          <div class="field">
            <span>
              ＠
            </span>

            <input
              id="admin-username"
              v-model="
                loginUsername
              "
              autocomplete="username"
              placeholder="輸入管理員帳號"
              required
            />
          </div>

          <label
            for="admin-password"
          >
            管理員密碼
          </label>

          <div class="field">
            <span>
              •••
            </span>

            <input
              id="admin-password"
              v-model="
                loginPassword
              "
              type="password"
              autocomplete="current-password"
              placeholder="輸入管理員密碼"
              required
            />
          </div>

          <button
            class="login-button"
            :disabled="
              loading
            "
          >
            <span
              v-if="
                loading
              "
              class="spinner"
            ></span>

            {{
              loading
                ? '驗證中…'
                : '登入管理中心'
            }}
          </button>

          <div
            v-if="
              loginError
            "
            class="login-error"
          >
            <strong>
              登入失敗
            </strong>

            <span>
              {{
                loginError
              }}
            </span>
          </div>
        </form>

        <div class="login-note">
          <span class="status-dot"></span>
          安全的管理員工作階段
        </div>
      </div>
    </div>
  </div>

  <!-- ========================
       主介面
  ========================= -->

  <div
    v-else
    class="admin-app"
  >
    <aside
      class="admin-sidebar"
      :class="{
        open:
          sidebarOpen
      }"
    >
      <div class="admin-brand">
        <div class="brand-icon">
          ▦
        </div>

        <div class="brand-copy">
          <strong>
            GGPT
          </strong>

          <small>
            管理後台
          </small>
        </div>
      </div>

      <div class="nav-caption">
        管理功能
      </div>

      <button
        class="nav-button"
        :class="{
          active:
            currentPage ===
            'users'
        }"
        @click="
          changePage(
            'users'
          )
        "
      >
        <span class="nav-icon">
          ◫
        </span>

        <span class="nav-copy">
          <strong>
            使用者與用量
          </strong>

          <small>
            會員、Token、模型
          </small>
        </span>

        <span class="nav-arrow">
          ›
        </span>
      </button>

      <button
        class="nav-button"
        :class="{
          active:
            currentPage ===
            'traffic'
        }"
        @click="
          changePage(
            'traffic'
          )
        "
      >
        <span class="nav-icon">
          ⌁
        </span>

        <span class="nav-copy">
          <strong>
            訪客分析
          </strong>

          <small>
            流量、來源、裝置
          </small>
        </span>

        <span class="nav-arrow">
          ›
        </span>
      </button>

      <div class="sidebar-spacer"></div>

      <div class="admin-account">
        <div class="account-avatar">
          {{
            String(
              adminName ||
              'A'
            )
              .slice(
                0,
                1
              )
              .toUpperCase()
          }}
        </div>

        <div class="account-copy">
          <strong>
            {{
              adminName
            }}
          </strong>

          <small>
            Administrator
          </small>
        </div>
      </div>

      <button
        class="logout-admin"
        @click="
          logout
        "
      >
        <span>
          ↪
        </span>

        登出管理中心
      </button>
    </aside>

    <div
      v-if="
        sidebarOpen
      "
      class="sidebar-backdrop"
      @click="
        sidebarOpen =
          false
      "
    ></div>

    <section class="admin-main">

      <!-- Topbar -->

      <header class="admin-topbar">
        <button
          class="mobile-admin-menu"
          @click="
            sidebarOpen =
              !sidebarOpen
          "
          aria-label="開啟側邊欄"
        >
          ☰
        </button>

        <div class="topbar-title">
          <span class="topbar-kicker">
            管理後台
          </span>

          <strong>
            {{
              currentPage ===
              'users'
                ? '使用者與用量'
                : '訪客分析'
            }}
          </strong>
        </div>

        <div class="topbar-actions">
          <span class="topbar-date">
            {{ currentDateLabel }}
          </span>

          <span class="sync-label">
            <span class="status-dot"></span>

            即時資料
          </span>

          <button
            class="refresh-button"
            @click="
              refreshCurrentPage
            "
            :disabled="
              loading
            "
          >
            <span
              :class="{
                spin:
                  loading
              }"
            >
              ↻
            </span>

            重新整理
          </button>
        </div>
      </header>

      <main class="admin-content">

        <!-- ========================
             使用者 / Token
        ========================= -->

        <section
          v-if="
            currentPage ===
            'users'
          "
        >
          <div class="page-heading">
            <div>
              <div class="eyebrow">
                總覽
              </div>

              <h1>
                使用者與用量
              </h1>

              <p>
                查看會員、模型、Token、思考 Token 與 API 使用金額。
              </p>
            </div>

            <div class="heading-actions">
              <button
                class="ghost-button"
                @click="
                  exportUsersCsv
                "
              >
                ↓ 匯出 CSV
              </button>

              <button
                class="primary-button"
                @click="
                  openNewUser
                "
              >
                ＋ 新增使用者
              </button>
            </div>
          </div>

          <!-- 統計卡 -->

          <div class="stats-grid">

            <article class="stat-card">
              <div class="stat-icon">
                ◉
              </div>

              <div class="stat-content">
                <span>
                  使用者
                </span>

                <strong>
                  {{
                    fmt(
                      summary.users
                    )
                  }}
                </strong>

                <small>
                  註冊帳號總數
                </small>
              </div>
            </article>

            <article class="stat-card">
              <div class="stat-icon blue">
                ◫
              </div>

              <div class="stat-content">
                <span>
                  對話
                </span>

                <strong>
                  {{
                    fmt(
                      summary.conversations
                    )
                  }}
                </strong>

                <small>
                  全部聊天室
                </small>
              </div>
            </article>

            <article class="stat-card">
              <div class="stat-icon violet">
                ⌁
              </div>

              <div class="stat-content">
                <span>
                  累計 Token
                </span>

                <strong>
                  {{
                    fmt(
                      summary.total_tokens
                    )
                  }}
                </strong>

                <small>
                  思考：
                  {{
                    fmt(
                      summary.reasoning_tokens
                    )
                  }}
                  Token
                </small>
              </div>
            </article>

            <!-- 本月金額 -->

            <article class="stat-card">
              <div class="stat-icon amber">
                $
              </div>

              <div class="stat-content cost-stat">
                <span>
                  本月使用金額
                </span>

                <strong>
                  {{
                    money(
                      monthConsumption
                    )
                  }}
                </strong>

                <div class="cost-breakdown">

                  <div>
                    <span>
                      API
                    </span>

                    <strong>
                      {{
                        money(
                          monthApiCost
                        )
                      }}
                    </strong>
                  </div>

                  <div>
                    <span>
                      思考
                    </span>

                    <strong>
                      {{
                        money(
                          summary.month_reasoning_cost
                        )
                      }}
                    </strong>
                  </div>

                  <div>
                    <span>
                      WebSearch
                    </span>

                    <strong>
                      {{
                        money(
                          webSearchCost
                        )
                      }}
                    </strong>
                  </div>

                </div>
              </div>
            </article>

          </div>

          <!-- 圖表 / 模型 -->

          <div class="section-grid">

            <!-- Token Chart -->

            <section class="panel large-panel">

              <div class="panel-head">
                <div>
                  <div class="eyebrow">
                    USAGE
                  </div>

                  <h2>
                    各模型總金額
                  </h2>

                </div>

                <button
                  class="collapse-button"
                  @click="
                    toggleSection(
                      'usage'
                    )
                  "
                >
                  {{
                    expandedSections
                      .usage
                      ? '收合'
                      : '展開'
                  }}
                </button>
              </div>

              <div
                v-show="
                  expandedSections
                    .usage
                "
                class="chart-container"
              >
                <canvas ref="usageCanvas"></canvas>
              </div>

            </section>

            <!-- Models -->

            <section class="panel">

              <div class="panel-head">
                <div>
                  <div class="eyebrow">
                    MODELS
                  </div>

                  <h2>
                    模型使用排行
                  </h2>

                  <small>
                    Token、思考與金額
                  </small>
                </div>

                <button
                  class="collapse-button"
                  @click="
                    toggleSection(
                      'models'
                    )
                  "
                >
                  {{
                    expandedSections
                      .models
                      ? '收合'
                      : '展開'
                  }}
                </button>
              </div>

              <div
                v-show="
                  expandedSections
                    .models
                "
              >

                <div
                  v-if="
                    !models.length
                  "
                  class="empty-state"
                >
                  <span>
                    ⌁
                  </span>

                  <strong>
                    尚無模型資料
                  </strong>

                  <small>
                    有 API 請求後會在這裡顯示排行。
                  </small>
                </div>

                <div
                  v-else
                  class="model-list"
                >
                  <div
                    v-for="
                      (
                        item,
                        index
                      ) in models
                    "
                    :key="
                      item.model
                    "
                    class="model-item"
                  >

                    <div class="model-row">

                      <div class="model-name-wrap">
                        <span class="rank">
                          {{
                            index +
                            1
                          }}
                        </span>

                        <strong>
                          {{
                            item.model
                          }}
                        </strong>
                      </div>

                      <strong>
                        {{
                          fmt(
                            item.total_tokens
                          )
                        }}
                      </strong>

                    </div>

                    <div class="progress">
                      <span
                        :style="{
                          width: `${(
                            Number(
                              item.total_tokens ||
                              0
                            ) /
                            maxModelTokens
                          ) * 100}%`
                        }"
                      ></span>
                    </div>

                    <div class="model-meta-grid">

                      <div>
                        <span>
                          API 請求
                        </span>

                        <strong>
                          {{
                            fmt(
                              item.requests
                            )
                          }}
                        </strong>
                      </div>

                      <div>
                        <span>
                          思考 Token
                        </span>

                        <strong>
                          {{
                            fmt(
                              item.reasoning_tokens
                            )
                          }}
                        </strong>
                      </div>

                      <div>
                        <span>
                          思考金額
                        </span>

                        <strong>
                          {{
                            money(
                              item.reasoning_cost
                            )
                          }}
                        </strong>
                      </div>

                      <div>
                        <span>
                          API 金額
                        </span>

                        <strong>
                          {{
                            money(
                              item.total_cost
                            )
                          }}
                        </strong>
                      </div>

                    </div>

                  </div>
                </div>

              </div>

            </section>

          </div>

          <!-- ========================
               使用者清單
          ========================= -->

          <section class="panel users-panel">

            <div class="panel-head user-panel-head">

              <div>
                <div class="eyebrow">
                  MEMBERS
                </div>

                <h2>
                  使用者清單
                </h2>

                <small>
                  共
                  {{
                    fmt(
                      users.length
                    )
                  }}
                  個帳號，
                  符合搜尋
                  {{
                    fmt(
                      sortedFilteredUsers.length
                    )
                  }}
                  個
                </small>
              </div>

              <button
                class="collapse-button"
                @click="
                  toggleSection(
                    'users'
                  )
                "
              >
                {{
                  expandedSections
                    .users
                    ? '收合'
                    : '展開'
                }}
              </button>

            </div>

            <div
              v-show="
                expandedSections
                  .users
              "
            >

              <div class="table-toolbar">

                <div
                  class="search-field"
                  :class="{
                    focused:
                      userSearchFocus
                  }"
                >

                  <span>
                    ⌕
                  </span>

                  <input
                    v-model="searchInput"
                    placeholder="搜尋帳號、名稱或 Email…"
                    @input="handleUserSearchInput"
                    @focus="
                      userSearchFocus =
                        true
                    "
                    @blur="
                      userSearchFocus =
                        false
                    "
                  />

                  <button
                    v-if="
                      searchInput
                    "
                    @click="clearUserSearch"
                    aria-label="清除搜尋"
                  >
                    ×
                  </button>

                </div>

                <div class="toolbar-actions">

                  <button
                    class="ghost-button"
                    @click="
                      exportUsersCsv
                    "
                  >
                    ↓ CSV
                  </button>

                  <button
                    class="primary-button compact"
                    @click="
                      openNewUser
                    "
                  >
                    ＋ 新增使用者
                  </button>

                </div>

              </div>

              <div
                v-if="
                  sortedFilteredUsers.length
                "
                class="table-scroll"
              >

                <table>

                  <thead>
                    <tr>

                      <th
                        @click="
                          toggleUserSort(
                            'display_name'
                          )
                        "
                      >
                        使用者{{
                          sortIndicator(
                            'display_name'
                          )
                        }}
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'auth_provider'
                          )
                        "
                      >
                        登入{{
                          sortIndicator(
                            'auth_provider'
                          )
                        }}
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'conversation_count'
                          )
                        "
                      >
                        對話{{
                          sortIndicator(
                            'conversation_count'
                          )
                        }}
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'message_count'
                          )
                        "
                      >
                        訊息{{
                          sortIndicator(
                            'message_count'
                          )
                        }}
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'api_requests'
                          )
                        "
                      >
                        請求{{
                          sortIndicator(
                            'api_requests'
                          )
                        }}
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'total_tokens'
                          )
                        "
                      >
                        Token{{
                          sortIndicator(
                            'total_tokens'
                          )
                        }}
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'reasoning_tokens'
                          )
                        "
                      >
                        思考 Token{{
                          sortIndicator(
                            'reasoning_tokens'
                          )
                        }}
                      </th>

                      <th>
                        思考金額
                      </th>

                      <th>
                        API 金額
                      </th>

                      <th>
                        Web Search 次數
                      </th>

                      <th>
                        Web Search 金額
                      </th>

                      <th
                        @click="
                          toggleUserSort(
                            'total_cost'
                          )
                        "
                      >
                        總金額{{
                          sortIndicator(
                            'total_cost'
                          )
                        }}
                      </th>

                      <th>
                        操作
                      </th>

                    </tr>
                  </thead>

                  <tbody>

                    <tr
                      v-for="
                        user in paginatedUsers
                      "
                      :key="
                        user.id
                      "
                    >

                      <!-- USER -->

                      <td>
                        <div class="user-cell">

                          <div class="table-avatar">
                            {{
                              String(
                                user.display_name ||
                                user.username ||
                                'U'
                              )
                                .slice(
                                  0,
                                  1
                                )
                                .toUpperCase()
                            }}
                          </div>

                          <div>
                            <strong>
                              {{
                                user.display_name ||
                                user.username
                              }}
                            </strong>

                            <small>
                              @{{
                                user.username
                              }}
                              · ID
                              {{
                                user.id
                              }}
                            </small>
                          </div>

                        </div>
                      </td>

                      <!-- LOGIN -->

                      <td>
                        <span class="badge">
                          {{
                            user.auth_provider ||
                            'local'
                          }}
                        </span>
                      </td>

                      <!-- Conversation -->

                      <td>
                        {{
                          fmt(
                            user.conversation_count
                          )
                        }}
                      </td>

                      <!-- Messages -->

                      <td>
                        {{
                          fmt(
                            user.message_count
                          )
                        }}
                      </td>

                      <!-- Requests -->

                      <td>
                        {{
                          fmt(
                            user.api_requests
                          )
                        }}
                      </td>

                      <!-- Total tokens -->

                      <td>
                        <strong>
                          {{
                            fmt(
                              user.total_tokens
                            )
                          }}
                        </strong>
                      </td>

                      <!-- Reasoning tokens -->

                      <td>
                        <strong class="reasoning-value">
                          {{
                            fmt(
                              user.reasoning_tokens
                            )
                          }}
                        </strong>
                      </td>

                      <!-- Reasoning cost -->

                      <td>
                        <span class="reasoning-cost">
                          {{
                            money(
                              user.reasoning_cost
                            )
                          }}
                        </span>
                      </td>

                      <!-- API cost -->

                      <td>
                        {{
                          money(
                            Math.max(
                              0,
                              Number(
                                user.total_cost ||
                                0
                              ) -
                              Number(
                                user.web_search_cost ||
                                0
                              )
                            )
                          )
                        }}
                      </td>

                      <!-- Web search -->

                      <td>
                        <strong>
                          {{
                            fmt(
                              user.web_search_calls
                            )
                          }}
                          次
                        </strong>
                      </td>

                      <td>
                        {{
                          money(
                            user.web_search_cost
                          )
                        }}
                      </td>

                      <!-- Total -->

                      <td>
                        <strong>
                          {{
                            money(
                              user.total_cost
                            )
                          }}
                        </strong>
                      </td>

                      <!-- Actions -->

                      <td>
                        <div class="actions">

                          <button
                            @click="
                              openEditUser(
                                user
                              )
                            "
                            title="編輯"
                          >
                            ✎
                          </button>

                          <button
                            class="danger"
                            @click="
                              deleteUser(
                                user
                              )
                            "
                            title="刪除"
                          >
                            ⌫
                          </button>

                        </div>
                      </td>

                    </tr>

                  </tbody>

                </table>

              </div>

              <div
                v-if="sortedFilteredUsers.length > USER_PAGE_SIZE"
                class="table-pagination"
              >
                <button
                  class="ghost-button"
                  :disabled="userPage <= 1"
                  @click="changeUserPage(userPage - 1)"
                >
                  上一頁
                </button>
                <span>
                  第 {{ userPage }} / {{ userPageCount }} 頁
                </span>
                <button
                  class="ghost-button"
                  :disabled="userPage >= userPageCount"
                  @click="changeUserPage(userPage + 1)"
                >
                  下一頁
                </button>
              </div>

              <div
                v-else
                class="empty-state table-empty"
              >
                <span>
                  ⌕
                </span>

                <strong>
                  找不到符合條件的使用者
                </strong>

                <small>
                  試試其他帳號、顯示名稱或 Email。
                </small>
              </div>

            </div>

          </section>

        </section>

        <!-- ========================
             TRAFFIC
        ========================= -->

        <section v-else>

          <div class="page-heading">
            <div>

              <div class="eyebrow">
                ANALYTICS
              </div>

              <h1>
                訪客分析
              </h1>

              <p>
                查看近 30 天流量、熱門頁面、來源與使用環境。
              </p>

            </div>
          </div>

          <div class="stats-grid">

            <article class="stat-card">

              <div class="stat-icon">
                ●
              </div>

              <div class="stat-content">

                <span>
                  今日訪客
                </span>

                <strong>
                  {{
                    fmt(
                      traffic.today
                        .unique_visitors
                    )
                  }}
                </strong>

                <small>
                  今日不重複訪客
                </small>

              </div>

            </article>

            <article class="stat-card">

              <div class="stat-icon blue">
                ◉
              </div>

              <div class="stat-content">

                <span>
                  今日瀏覽
                </span>

                <strong>
                  {{
                    fmt(
                      traffic.today
                        .page_views
                    )
                  }}
                </strong>

                <small>
                  今日頁面瀏覽
                </small>

              </div>

            </article>

            <article class="stat-card">

              <div class="stat-icon violet">
                ◌
              </div>

              <div class="stat-content">

                <span>
                  30 天訪客
                </span>

                <strong>
                  {{
                    fmt(
                      traffic.summary
                        .unique_visitors
                    )
                  }}
                </strong>

                <small>
                  近 30 天不重複訪客
                </small>

              </div>

            </article>

            <article class="stat-card">

              <div class="stat-icon amber">
                ◍
              </div>

              <div class="stat-content">

                <span>
                  30 天瀏覽
                </span>

                <strong>
                  {{
                    fmt(
                      traffic.summary
                        .page_views
                    )
                  }}
                </strong>

                <small>
                  近 30 天頁面瀏覽
                </small>

              </div>

            </article>

          </div>

          <div class="section-grid">

            <section class="panel large-panel">

              <div class="panel-head">

                <div>
                  <div class="eyebrow">
                    TRAFFIC
                  </div>

                  <h2>
                    近 30 天訪客流量
                  </h2>

                  <small>
                    瀏覽量 / 不重複訪客
                  </small>
                </div>

              </div>

              <div class="chart-container">
                <canvas ref="trafficCanvas"></canvas>
              </div>

            </section>

            <section class="panel">

              <div class="panel-head">
                <div>

                  <div class="eyebrow">
                    PAGES
                  </div>

                  <h2>
                    熱門頁面
                  </h2>

                  <small>
                    近 30 天
                  </small>

                </div>
              </div>

              <div
                v-if="
                  traffic.pages.length
                "
                class="metric-list"
              >

                <div
                  v-for="
                    item in traffic.pages
                  "
                  :key="
                    item.path
                  "
                  class="metric-item"
                >

                  <div>

                    <strong>
                      {{
                        item.path
                      }}
                    </strong>

                    <small>
                      {{
                        fmt(
                          item.visitors
                        )
                      }}
                      位訪客
                    </small>

                  </div>

                  <b>
                    {{
                      fmt(
                        item.views
                      )
                    }}
                  </b>

                </div>

              </div>

              <div
                v-else
                class="empty-state"
              >

                <span>
                  ◌
                </span>

                <strong>
                  尚無頁面資料
                </strong>

                <small>
                  收到流量後會顯示熱門頁面。
                </small>

              </div>

            </section>

          </div>

          <!-- SOURCES / DEVICES / BROWSERS -->

          <div class="three-columns">

            <section class="panel">

              <div class="panel-head">
                <div>

                  <div class="eyebrow">
                    SOURCES
                  </div>

                  <h2>
                    訪客來源
                  </h2>

                </div>
              </div>

              <div class="metric-list">

                <div
                  v-for="
                    item in traffic.sources
                  "
                  :key="
                    item.source
                  "
                  class="metric-item"
                >

                  <div>

                    <strong>
                      {{
                        item.source
                      }}
                    </strong>

                    <small>
                      {{
                        fmt(
                          item.visitors
                        )
                      }}
                      位訪客
                    </small>

                  </div>

                  <b>
                    {{
                      fmt(
                        item.views
                      )
                    }}
                  </b>

                </div>

              </div>

            </section>

            <section class="panel">

              <div class="panel-head">
                <div>

                  <div class="eyebrow">
                    DEVICES
                  </div>

                  <h2>
                    裝置
                  </h2>

                </div>
              </div>

              <div class="metric-list">

                <div
                  v-for="
                    item in traffic.devices
                  "
                  :key="
                    item.device_type
                  "
                  class="metric-item"
                >

                  <div>

                    <strong>
                      {{
                        item.device_type
                      }}
                    </strong>

                    <small>
                      {{
                        fmt(
                          item.visitors
                        )
                      }}
                      位訪客
                    </small>

                  </div>

                  <b>
                    {{
                      fmt(
                        item.views
                      )
                    }}
                  </b>

                </div>

              </div>

            </section>

            <section class="panel">

              <div class="panel-head">
                <div>

                  <div class="eyebrow">
                    BROWSERS
                  </div>

                  <h2>
                    瀏覽器
                  </h2>

                </div>
              </div>

              <div class="metric-list">

                <div
                  v-for="
                    item in traffic.browsers
                  "
                  :key="
                    item.browser
                  "
                  class="metric-item"
                >

                  <div>

                    <strong>
                      {{
                        item.browser
                      }}
                    </strong>

                    <small>
                      {{
                        fmt(
                          item.visitors
                        )
                      }}
                      位訪客
                    </small>

                  </div>

                  <b>
                    {{
                      fmt(
                        item.views
                      )
                    }}
                  </b>

                </div>

              </div>

            </section>

          </div>

          <!-- SYSTEM -->

          <div class="section-grid">

            <section class="panel">

              <div class="panel-head">
                <div>

                  <div class="eyebrow">
                    SYSTEMS
                  </div>

                  <h2>
                    作業系統
                  </h2>

                </div>
              </div>

              <div class="metric-list">

                <div
                  v-for="
                    item in traffic.systems
                  "
                  :key="
                    item.os
                  "
                  class="metric-item"
                >

                  <div>

                    <strong>
                      {{
                        item.os
                      }}
                    </strong>

                    <small>
                      {{
                        fmt(
                          item.visitors
                        )
                      }}
                      位訪客
                    </small>

                  </div>

                  <b>
                    {{
                      fmt(
                        item.views
                      )
                    }}
                  </b>

                </div>

              </div>

            </section>

          </div>

          <!-- RECENT -->

          <section class="panel recent-panel">

            <div class="panel-head">
              <div>

                <div class="eyebrow">
                  LIVE LOG
                </div>

                <h2>
                  最近訪客
                </h2>

                <small>
                  最近 50 筆
                </small>

              </div>
            </div>

            <div
              v-if="
                traffic.recent.length
              "
              class="table-scroll"
            >

              <table class="recent-table">

                <thead>
                  <tr>
                    <th>
                      時間
                    </th>

                    <th>
                      頁面
                    </th>

                    <th>
                      裝置
                    </th>

                    <th>
                      瀏覽器
                    </th>

                    <th>
                      OS
                    </th>

                    <th>
                      來源
                    </th>
                  </tr>
                </thead>

                <tbody>

                  <tr
                    v-for="
                      item in traffic.recent
                    "
                    :key="
                      item.id
                    "
                  >

                    <td>
                      {{
                        formatDate(
                          item.created_at
                        )
                      }}
                    </td>

                    <td>
                      <span class="tag">
                        {{
                          item.path
                        }}
                      </span>
                    </td>

                    <td>
                      {{
                        item.device_type
                      }}
                    </td>

                    <td>
                      {{
                        item.browser
                      }}
                    </td>

                    <td>
                      {{
                        item.os
                      }}
                    </td>

                    <td class="referrer">
                      {{
                        item.referrer ||
                        '直接訪問 / 無來源'
                      }}
                    </td>

                  </tr>

                </tbody>

              </table>

            </div>

            <div
              v-else
              class="empty-state table-empty"
            >

              <span>
                ⌁
              </span>

              <strong>
                尚無訪客紀錄
              </strong>

              <small>
                收到新的頁面請求後會顯示於此。
              </small>

            </div>

          </section>

        </section>

      </main>

    </section>

    <!-- ========================
         USER MODAL
    ========================= -->

    <div
      v-if="
        userModal
      "
      class="modal"
      @click.self="
        closeUserModal
      "
    >
      <div class="modal-box">

        <div class="modal-header">

          <div>

            <div class="eyebrow">
              {{
                editingUser
                  ? 'EDIT MEMBER'
                  : 'NEW MEMBER'
              }}
            </div>

            <h2>
              {{
                editingUser
                  ? '修改使用者'
                  : '新增使用者'
              }}
            </h2>

          </div>

          <button
            @click="
              closeUserModal
            "
          >
            ×
          </button>

        </div>

        <form
          @submit.prevent="
            saveUser
          "
          class="modal-form"
        >

          <label>
            帳號
          </label>

          <input
            v-model="
              form.username
            "
            minlength="3"
            maxlength="32"
            placeholder="登入帳號"
            required
          />

          <label>
            密碼
          </label>

          <input
            v-model="
              form.password
            "
            type="password"
            minlength="8"
            maxlength="128"
            :required="
              !editingUser
            "
            placeholder="輸入密碼"
          />

          <small
            v-if="
              editingUser
            "
          >
            編輯時留空＝保留原密碼
          </small>

          <label>
            顯示名稱
          </label>

          <input
            v-model="
              form.display_name
            "
            maxlength="255"
            placeholder="例如：王小明"
          />

          <label>
            Email
          </label>

          <input
            v-model="
              form.email
            "
            type="email"
            maxlength="320"
            placeholder="name@example.com"
          />

          <div class="modal-actions">

            <button
              type="button"
              class="ghost-button"
              @click="
                closeUserModal
              "
            >
              取消
            </button>

            <button
              class="primary-button"
              :disabled="
                loading
              "
            >
              {{
                loading
                  ? '儲存中…'
                  : '儲存變更'
              }}
            </button>

          </div>

        </form>

      </div>
    </div>

    <!-- TOAST -->

    <div
      class="toast"
      :class="{
        show:
          toastVisible
      }"
    >
      <span class="toast-dot"></span>

      {{
        toastText
      }}
    </div>

  </div>
</template>

<style scoped>
:global(*) {
  box-sizing: border-box;
}

:global(body) {
  margin: 0;
  background: #07111f;
}

:global(button),
:global(input) {
  font: inherit;
}

.admin-app {
  min-height: 100dvh;
  display: flex;
  color: #edf4ff;

  background:
    radial-gradient(
      circle at 8% 0%,
      rgba(63,145,255,.12),
      transparent 30%
    ),
    radial-gradient(
      circle at 90% 10%,
      rgba(70,211,164,.08),
      transparent 26%
    ),
    #07111f;

  font-family:
    Inter,
    "Noto Sans TC",
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
}

/* =========================
   SIDEBAR
========================= */

.admin-sidebar {
  width: 264px;
  flex: 0 0 264px;

  position: sticky;
  top: 0;

  height: 100dvh;

  display: flex;
  flex-direction: column;

  gap: 4px;

  padding:
    18px
    14px;

  background:
    rgba(
      7,
      16,
      30,
      .94
    );

  border-right:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  backdrop-filter:
    blur(20px);

  z-index: 50;
}

.admin-brand {
  display: flex;
  align-items: center;

  gap: 11px;

  padding:
    2px
    8px
    24px;
}

.brand-icon,
.login-mark {
  width: 42px;
  height: 42px;

  display: grid;
  place-items: center;

  border-radius:
    13px;

  color:
    #04121f;

  background:
    linear-gradient(
      135deg,
      #73f2c2,
      #5ea7ff
    );

  box-shadow:
    0
    10px
    25px
    rgba(
      91,
      170,
      255,
      .15
    );

  font-weight: 900;
}

.brand-copy strong {
  display: block;

  font-size: 16px;

  letter-spacing:
    .02em;
}

.brand-copy small {
  display: block;

  margin-top: 2px;

  color:
    #6e7f98;

  font-size:
    10px;
}

.nav-caption {
  padding:
    8px
    10px;

  color:
    #51647e;

  font-size:
    10px;

  text-transform:
    uppercase;

  letter-spacing:
    .16em;
}

.nav-button {
  width: 100%;
  min-height: 64px;

  display: flex;
  align-items: center;

  gap: 11px;

  padding:
    10px;

  border:
    1px solid
    transparent;

  border-radius:
    13px;

  background:
    transparent;

  color:
    #9eaec4;

  cursor:
    pointer;

  text-align:
    left;

  transition:
    .18s ease;
}

.nav-button:hover {
  background:
    rgba(
      255,
      255,
      255,
      .035
    );

  color:
    #eff5ff;
}

.nav-button.active {
  background:
    linear-gradient(
      90deg,
      rgba(
        88,
        152,
        255,
        .16
      ),
      rgba(
        88,
        152,
        255,
        .04
      )
    );

  border-color:
    rgba(
      102,
      167,
      255,
      .18
    );

  color:
    #f4f8ff;
}

.nav-icon {
  width: 35px;
  height: 35px;

  flex:
    0 0 35px;

  display:
    grid;

  place-items:
    center;

  border-radius:
    10px;

  background:
    rgba(
      255,
      255,
      255,
      .045
    );

  font-size:
    17px;
}

.nav-button.active
.nav-icon {
  background:
    rgba(
      97,
      161,
      255,
      .16
    );

  color:
    #9ecbff;
}

.nav-copy {
  flex: 1;
  min-width: 0;
}

.nav-copy strong,
.nav-copy small {
  display:
    block;
}

.nav-copy strong {
  font-size:
    12px;
}

.nav-copy small {
  margin-top:
    4px;

  color:
    #657993;

  font-size:
    10px;
}

.nav-arrow {
  opacity:
    .45;

  font-size:
    22px;
}

.sidebar-spacer {
  flex: 1;
}

/* =========================
   ACCOUNT
========================= */

.admin-account {
  display:
    flex;

  align-items:
    center;

  gap:
    10px;

  padding:
    12px;

  margin-bottom:
    8px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .06
    );

  border-radius:
    13px;

  background:
    rgba(
      255,
      255,
      255,
      .025
    );
}

.account-avatar,
.table-avatar {
  display:
    grid;

  place-items:
    center;

  border-radius:
    10px;

  background:
    linear-gradient(
      145deg,
      #27435f,
      #13233a
    );

  color:
    #cde3ff;

  font-weight:
    800;
}

.account-avatar {
  width:
    35px;

  height:
    35px;

  font-size:
    13px;
}

.account-copy {
  min-width:
    0;
}

.account-copy strong,
.account-copy small {
  display:
    block;

  overflow:
    hidden;

  text-overflow:
    ellipsis;

  white-space:
    nowrap;
}

.account-copy strong {
  font-size:
    12px;
}

.account-copy small {
  margin-top:
    2px;

  color:
    #61718a;

  font-size:
    10px;
}

.logout-admin {
  height:
    42px;

  display:
    flex;

  align-items:
    center;

  justify-content:
    center;

  gap:
    8px;

  border:
    1px solid
    rgba(
      246,
      129,
      145,
      .2
    );

  border-radius:
    11px;

  background:
    rgba(
      246,
      129,
      145,
      .06
    );

  color:
    #ffb8c2;

  cursor:
    pointer;
}

/* =========================
   MAIN
========================= */

.admin-main {
  min-width:
    0;

  flex:
    1;
}

.admin-topbar {
  height:
    74px;

  position:
    sticky;

  top:
    0;

  z-index:
    30;

  display:
    flex;

  align-items:
    center;

  gap:
    14px;

  padding:
    0
    28px;

  background:
    rgba(
      7,
      17,
      31,
      .72
    );

  border-bottom:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  backdrop-filter:
    blur(18px);
}

.topbar-title
.topbar-kicker,
.eyebrow {
  display:
    block;

  color:
    #5f7899;

  font-size:
    9px;

  font-weight:
    800;

  letter-spacing:
    .18em;

  text-transform:
    uppercase;
}

.topbar-title
strong {
  display:
    block;

  margin-top:
    3px;

  font-size:
    16px;
}

.topbar-actions {
  margin-left:
    auto;

  display:
    flex;

  align-items:
    center;

  gap:
    10px;
}

.sync-label {
  display:
    inline-flex;

  gap:
    7px;

  align-items:
    center;

  color:
    #8598b1;

  font-size:
    11px;
}

.status-dot {
  width:
    7px;

  height:
    7px;

  border-radius:
    50%;

  background:
    #4ee0ad;

  box-shadow:
    0
    0
    0
    4px
    rgba(
      78,
      224,
      173,
      .08
    );
}

/* =========================
   BUTTON
========================= */

.refresh-button,
.ghost-button,
.primary-button {
  height:
    40px;

  display:
    inline-flex;

  align-items:
    center;

  justify-content:
    center;

  gap:
    7px;

  padding:
    0
    13px;

  border-radius:
    10px;

  cursor:
    pointer;

  transition:
    .18s ease;
}

.refresh-button,
.ghost-button {
  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .09
    );

  background:
    rgba(
      255,
      255,
      255,
      .035
    );

  color:
    #c9d6e8;
}

.refresh-button:hover,
.ghost-button:hover {
  background:
    rgba(
      255,
      255,
      255,
      .06
    );
}

.primary-button {
  border:
    0;

  background:
    linear-gradient(
      135deg,
      #6be7bb,
      #60a6ff
    );

  color:
    #05121e;

  font-weight:
    800;

  box-shadow:
    0
    10px
    22px
    rgba(
      87,
      169,
      255,
      .12
    );
}

.primary-button:hover {
  filter:
    brightness(
      1.04
    );

  transform:
    translateY(
      -1px
    );
}

.primary-button.compact {
  height:
    38px;
}

.mobile-admin-menu {
  display:
    none;
}

/* =========================
   CONTENT
========================= */

.admin-content {
  width:
    min(
      1540px,
      100%
    );

  margin:
    0 auto;

  padding:
    28px
    28px
    48px;
}

.page-heading {
  display:
    flex;

  align-items:
    flex-end;

  justify-content:
    space-between;

  gap:
    20px;

  margin-bottom:
    22px;
}

.page-heading h1 {
  margin:
    7px
    0
    7px;

  font-size:
    32px;

  letter-spacing:
    -.03em;
}

.page-heading p {
  margin:
    0;

  color:
    #8091a9;

  font-size:
    13px;
}

.heading-actions {
  display:
    flex;

  gap:
    9px;
}

/* =========================
   STATS
========================= */

.stats-grid {
  display:
    grid;

  grid-template-columns:
    repeat(
      4,
      minmax(
        0,
        1fr
      )
    );

  gap:
    12px;
}

.stat-card {
  min-width:
    0;

  display:
    flex;

  align-items:
    center;

  gap:
    13px;

  padding:
    17px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  border-radius:
    16px;

  background:
    linear-gradient(
      180deg,
      rgba(
        19,
        35,
        57,
        .88
      ),
      rgba(
        10,
        23,
        39,
        .92
      )
    );

  box-shadow:
    0
    14px
    32px
    rgba(
      0,
      0,
      0,
      .08
    );
}

.stat-icon {
  width:
    42px;

  height:
    42px;

  flex:
    0 0 42px;

  display:
    grid;

  place-items:
    center;

  border-radius:
    12px;

  background:
    rgba(
      94,
      227,
      183,
      .12
    );

  color:
    #7de6c3;
}

.stat-icon.blue {
  background:
    rgba(
      95,
      164,
      255,
      .12
    );

  color:
    #9dc9ff;
}

.stat-icon.violet {
  background:
    rgba(
      174,
      132,
      255,
      .12
    );

  color:
    #cab1ff;
}

.stat-icon.amber {
  background:
    rgba(
      255,
      193,
      93,
      .12
    );

  color:
    #ffd28a;
}

.stat-content {
  min-width:
    0;
}

.stat-content >
span,
.stat-content >
small {
  display:
    block;
}

.stat-content >
span {
  color:
    #9eb0c6;

  font-size:
    11px;
}

.stat-content >
strong {
  display:
    block;

  margin-top:
    6px;

  font-size:
    24px;

  line-height:
    1;

  letter-spacing:
    -.03em;
}

.stat-content >
small {
  margin-top:
    6px;

  color:
    #667b96;

  font-size:
    9px;

  white-space:
    nowrap;

  overflow:
    hidden;

  text-overflow:
    ellipsis;
}

.cost-stat {
  width:
    100%;
}

/* =========================
   COST
========================= */

.cost-breakdown {
  display:
    grid;

  grid-template-columns:
    repeat(
      3,
      minmax(
        0,
        1fr
      )
    );

  gap:
    8px;

  margin-top:
    10px;
}

.cost-breakdown >
div {
  padding:
    8px
    10px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  border-radius:
    10px;

  background:
    rgba(
      255,
      255,
      255,
      .025
    );
}

.cost-breakdown
span {
  display:
    block;

  font-size:
    11px;

  color:
    #77839a;

  margin-bottom:
    4px;
}

.cost-breakdown
strong {
  display:
    block;

  font-size:
    14px;
}

/* =========================
   PANELS
========================= */

.section-grid {
  display:
    grid;

  grid-template-columns:
    minmax(
      0,
      1.55fr
    )
    minmax(
      340px,
      .85fr
    );

  gap:
    14px;

  margin-top:
    14px;
}

.three-columns {
  display:
    grid;

  grid-template-columns:
    repeat(
      3,
      minmax(
        0,
        1fr
      )
    );

  gap:
    14px;

  margin-top:
    14px;
}

.panel {
  min-width:
    0;

  padding:
    17px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  border-radius:
    16px;

  background:
    rgba(
      13,
      28,
      46,
      .88
    );

  box-shadow:
    0
    16px
    38px
    rgba(
      0,
      0,
      0,
      .09
    );
}

.panel-head {
  display:
    flex;

  align-items:
    flex-start;

  justify-content:
    space-between;

  gap:
    12px;

  margin-bottom:
    15px;
}

.panel-head h2 {
  margin:
    4px
    0;

  font-size:
    15px;
}

.panel-head small {
  color:
    #72859f;

  font-size:
    10px;
}

.collapse-button {
  height:
    28px;

  padding:
    0
    9px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  border-radius:
    8px;

  background:
    rgba(
      255,
      255,
      255,
      .028
    );

  color:
    #7588a1;

  font-size:
    10px;

  cursor:
    pointer;
}

.chart-container {
  height:
    600px;
  position:
    relative;
}

/* =========================
   MODELS
========================= */

.model-list,
.metric-list {
  display:
    flex;

  flex-direction:
    column;

  gap:
    8px;
}

.model-item,
.metric-item {
  padding:
    10px
    11px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .06
    );

  border-radius:
    11px;

  background:
    rgba(
      255,
      255,
      255,
      .018
    );
}

.model-row,
.metric-item {
  display:
    flex;

  align-items:
    center;

  justify-content:
    space-between;

  gap:
    10px;
}

.model-name-wrap {
  display:
    flex;

  align-items:
    center;

  gap:
    8px;

  min-width:
    0;
}

.rank {
  width:
    22px;

  height:
    22px;

  display:
    grid;

  place-items:
    center;

  border-radius:
    7px;

  background:
    rgba(
      95,
      164,
      255,
      .09
    );

  color:
    #8ebeff;

  font-size:
    9px;

  font-weight:
    800;
}

.progress {
  height:
    6px;

  margin-top:
    10px;

  border-radius:
    999px;

  overflow:
    hidden;

  background:
    #16273e;
}

.progress span {
  display:
    block;

  height:
    100%;

  border-radius:
    inherit;

  background:
    linear-gradient(
      90deg,
      #5fa8ff,
      #6fe2bc
    );
}

.model-meta-grid {
  display:
    grid;

  grid-template-columns:
    repeat(
      2,
      minmax(
        0,
        1fr
      )
    );

  gap:
    7px;

  margin-top:
    10px;
}

.model-meta-grid >
div {
  padding:
    7px
    9px;

  border-radius:
    8px;

  background:
    rgba(
      255,
      255,
      255,
      .025
    );
}

.model-meta-grid
span {
  display:
    block;

  color:
    #71859f;

  font-size:
    10px;

  margin-bottom:
    3px;
}

.model-meta-grid
strong {
  font-size:
    11px;

  color:
    #dbe7f7;
}

/* =========================
   USERS TABLE
========================= */

.users-panel {
  margin-top:
    14px;
}

.user-panel-head {
  align-items:
    center;
}

.table-toolbar {
  display:
    flex;

  align-items:
    center;

  justify-content:
    space-between;

  gap:
    10px;

  margin-bottom:
    13px;
}

.search-field {
  width:
    min(
      520px,
      100%
    );

  height:
    42px;

  display:
    flex;

  align-items:
    center;

  gap:
    9px;

  padding:
    0
    11px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .08
    );

  border-radius:
    11px;

  background:
    #091726;

  transition:
    .18s ease;
}

.search-field.focused {
  border-color:
    rgba(
      104,
      168,
      255,
      .42
    );

  box-shadow:
    0
    0
    0
    3px
    rgba(
      104,
      168,
      255,
      .08
    );
}

.search-field span {
  color:
    #6f84a0;

  font-size:
    17px;
}

.search-field input {
  flex:
    1;

  min-width:
    0;

  border:
    0;

  outline:
    0;

  background:
    transparent;

  color:
    #edf4ff;
}

.search-field button {
  border:
    0;

  background:
    transparent;

  color:
    #72859e;

  cursor:
    pointer;
}

.toolbar-actions {
  display:
    flex;

  gap:
    8px;
}

.table-scroll {
  overflow:
    auto;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .07
    );

  border-radius:
    13px;
}

.table-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 12px;
  color: #8fa1b8;
  font-size: 11px;
}

.table-pagination button:disabled {
  cursor: not-allowed;
  opacity: .45;
}

table {
  width:
    100%;

  min-width:
    1250px;

  border-collapse:
    collapse;
}

th,
td {
  padding:
    12px;

  border-bottom:
    1px solid
    rgba(
      255,
      255,
      255,
      .055
    );

  color:
    #b7c4d7;

  font-size:
    10px;

  text-align:
    left;
}

th {
  position:
    sticky;

  top:
    0;

  z-index:
    1;

  background:
    #102036;

  color:
    #7287a0;

  font-size:
    9px;

  text-transform:
    uppercase;

  letter-spacing:
    .04em;

  cursor:
    pointer;
}

tbody tr:hover {
  background:
    rgba(
      255,
      255,
      255,
      .018
    );
}

tbody tr:last-child td {
  border-bottom:
    0;
}

.user-cell {
  display:
    flex;

  align-items:
    center;

  gap:
    9px;

  min-width:
    210px;
}

.table-avatar {
  width:
    32px;

  height:
    32px;

  flex:
    0 0 32px;

  font-size:
    11px;
}

.user-cell strong,
.user-cell small {
  display:
    block;
}

.user-cell strong {
  color:
    #e9f1fc;

  font-size:
    10px;
}

.user-cell small {
  margin-top:
    3px;

  color:
    #667a96;

  font-size:
    9px;
}

.reasoning-value {
  color:
    #c6b4ff;
}

.reasoning-cost {
  color:
    #d7caff;
}

/* =========================
   BADGES
========================= */

.badge,
.tag {
  display:
    inline-flex;

  align-items:
    center;

  padding:
    4px
    8px;

  border:
    1px solid
    rgba(
      104,
      168,
      255,
      .1
    );

  border-radius:
    999px;

  background:
    rgba(
      104,
      168,
      255,
      .08
    );

  color:
    #9fc9ff;

  font-size:
    9px;
}

/* =========================
   ACTIONS
========================= */

.actions {
  display:
    flex;

  gap:
    6px;
}

.actions button {
  width:
    31px;

  height:
    31px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .08
    );

  border-radius:
    9px;

  background:
    rgba(
      255,
      255,
      255,
      .025
    );

  color:
    #aebed1;

  cursor:
    pointer;
}

.actions button:hover {
  background:
    rgba(
      255,
      255,
      255,
      .06
    );

  color:
    #fff;
}

.actions
.danger:hover {
  border-color:
    rgba(
      251,
      113,
      133,
      .25
    );

  background:
    rgba(
      251,
      113,
      133,
      .08
    );

  color:
    #ffc1ca;
}

/* =========================
   METRIC
========================= */

.metric-item strong {
  display:
    block;

  color:
    #e2ebf8;

  font-size:
    10px;
}

.metric-item small {
  display:
    block;

  margin-top:
    4px;

  color:
    #657993;

  font-size:
    9px;
}

.metric-item b {
  color:
    #9cc6ff;

  font-size:
    11px;
}

.referrer {
  max-width:
    310px;

  overflow:
    hidden;

  white-space:
    nowrap;

  text-overflow:
    ellipsis;
}

.recent-panel {
  margin-top:
    14px;
}

.recent-table th {
  cursor:
    default;
}

/* =========================
   EMPTY
========================= */

.empty-state {
  min-height:
    180px;

  display:
    flex;

  flex-direction:
    column;

  align-items:
    center;

  justify-content:
    center;

  gap:
    7px;

  color:
    #6f839d;

  text-align:
    center;
}

.empty-state span {
  font-size:
    24px;

  opacity:
    .65;
}

.empty-state strong {
  color:
    #afbdd0;

  font-size:
    12px;
}

.empty-state small {
  color:
    #667990;

  font-size:
    10px;
}

.table-empty {
  padding:
    22px;
}

/* =========================
   MODAL
========================= */

.modal {
  position:
    fixed;

  inset:
    0;

  z-index:
    100;

  display:
    grid;

  place-items:
    center;

  padding:
    18px;

  background:
    rgba(
      0,
      7,
      17,
      .68
    );

  backdrop-filter:
    blur(8px);
}

.modal-box {
  width:
    min(
      520px,
      100%
    );

  padding:
    20px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .09
    );

  border-radius:
    17px;

  background:
    #0e1c30;

  box-shadow:
    0
    30px
    80px
    rgba(
      0,
      0,
      0,
      .34
    );
}

.modal-header {
  display:
    flex;

  justify-content:
    space-between;

  align-items:
    flex-start;

  gap:
    10px;

  margin-bottom:
    16px;
}

.modal-header h2 {
  margin:
    5px
    0
    0;

  font-size:
    20px;
}

.modal-header button {
  border:
    0;

  background:
    transparent;

  color:
    #8396ae;

  font-size:
    22px;

  cursor:
    pointer;
}

.modal-form label {
  display:
    block;

  margin:
    12px
    0
    6px;

  color:
    #8ea0b8;

  font-size:
    10px;
}

.modal-form input {
  width:
    100%;

  height:
    44px;

  padding:
    0
    12px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .09
    );

  border-radius:
    10px;

  outline:
    0;

  background:
    #091726;

  color:
    #eef5ff;
}

.modal-form input:focus {
  border-color:
    rgba(
      104,
      168,
      255,
      .4
    );

  box-shadow:
    0
    0
    0
    3px
    rgba(
      104,
      168,
      255,
      .07
    );
}

.modal-form >
small {
  display:
    block;

  margin-top:
    5px;

  color:
    #5e738f;

  font-size:
    9px;
}

.modal-actions {
  display:
    flex;

  justify-content:
    flex-end;

  gap:
    8px;

  margin-top:
    18px;
}

/* =========================
   TOAST
========================= */

.toast {
  position:
    fixed;

  left:
    50%;

  bottom:
    22px;

  z-index:
    200;

  display:
    inline-flex;

  align-items:
    center;

  gap:
    8px;

  max-width:
    min(
      500px,
      calc(
        100vw -
        24px
      )
    );

  padding:
    10px
    14px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .08
    );

  border-radius:
    10px;

  background:
    rgba(
      17,
      31,
      51,
      .96
    );

  color:
    #f3f7ff;

  font-size:
    11px;

  opacity:
    0;

  pointer-events:
    none;

  transform:
    translate(
      -50%,
      10px
    );

  transition:
    .2s ease;

  box-shadow:
    0
    18px
    36px
    rgba(
      0,
      0,
      0,
      .25
    );
}

.toast.show {
  opacity:
    1;

  transform:
    translate(
      -50%,
      0
    );
}

.toast-dot {
  width:
    7px;

  height:
    7px;

  border-radius:
    50%;

  background:
    #5fe0b5;
}

/* =========================
   LOGIN
========================= */

.login-shell {
  min-height:
    100dvh;

  display:
    grid;

  place-items:
    center;

  padding:
    24px;

  position:
    relative;

  overflow:
    hidden;

  background:
    #07111f;

  color:
    #edf4ff;

  font-family:
    Inter,
    "Noto Sans TC",
    system-ui,
    sans-serif;
}

.login-box {
  width:
    min(
      440px,
      100%
    );

  position:
    relative;

  z-index:
    2;

  padding:
    31px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .08
    );

  border-radius:
    21px;

  background:
    rgba(
      13,
      28,
      46,
      .94
    );

  backdrop-filter:
    blur(24px);

  box-shadow:
    0
    30px
    100px
    rgba(
      0,
      0,
      0,
      .3
    );
}

.admin-mark {
  margin-bottom:
    16px;
}

.login-box h1 {
  margin:
    7px
    0
    7px;

  font-size:
    28px;

  letter-spacing:
    -.03em;
}

.login-box p {
  margin:
    0;

  color:
    #7f92ac;

  font-size:
    12px;

  line-height:
    1.7;
}

.login-form {
  margin-top:
    22px;
}

.login-form label {
  display:
    block;

  margin:
    12px
    0
    6px;

  color:
    #91a2b8;

  font-size:
    10px;
}

.field {
  height:
    46px;

  display:
    flex;

  align-items:
    center;

  gap:
    8px;

  padding:
    0
    12px;

  border:
    1px solid
    rgba(
      255,
      255,
      255,
      .08
    );

  border-radius:
    11px;

  background:
    #091726;
}

.field span {
  color:
    #60758f;

  font-size:
    11px;
}

.field input {
  flex:
    1;

  min-width:
    0;

  border:
    0;

  outline:
    0;

  background:
    transparent;

  color:
    #eef5ff;
}

.login-button {
  width:
    100%;

  height:
    46px;

  margin-top:
    14px;

  border:
    0;

  border-radius:
    11px;

  background:
    linear-gradient(
      135deg,
      #6fe8bb,
      #63a9ff
    );

  color:
    #04121e;

  font-weight:
    900;

  cursor:
    pointer;
}

.login-button:disabled {
  opacity:
    .65;

  cursor:
    default;
}

.login-error {
  display:
    flex;

  flex-direction:
    column;

  gap:
    4px;

  margin-top:
    11px;

  padding:
    10px
    11px;

  border:
    1px solid
    rgba(
      248,
      113,
      133,
      .16
    );

  border-radius:
    10px;

  background:
    rgba(
      248,
      113,
      133,
      .06
    );

  color:
    #fbb2be;

  font-size:
    10px;
}

.login-note {
  display:
    flex;

  align-items:
    center;

  gap:
    8px;

  margin-top:
    18px;

  color:
    #566c87;

  font-size:
    9px;
}

.login-glow {
  position:
    absolute;

  width:
    360px;

  height:
    360px;

  border-radius:
    50%;

  filter:
    blur(
      80px
    );

  opacity:
    .22;
}

.glow-a {
  top:
    -160px;

  left:
    -120px;

  background:
    #3a9cff;
}

.glow-b {
  right:
    -130px;

  bottom:
    -160px;

  background:
    #42d7a7;
}

.spinner {
  width:
    14px;

  height:
    14px;

  border:
    2px solid
    rgba(
      4,
      18,
      30,
      .2
    );

  border-top-color:
    #04121e;

  border-radius:
    50%;

  animation:
    spin
    .8s
    linear
    infinite;
}

.spin {
  display:
    inline-block;

  animation:
    spin
    .8s
    linear
    infinite;
}

@keyframes spin {
  to {
    transform:
      rotate(
        360deg
      );
  }
}

.sidebar-backdrop {
  display:
    none;
}

/* =========================
   RESPONSIVE
========================= */

@media (
  max-width:
  1180px
) {
  .stats-grid {
    grid-template-columns:
      repeat(
        2,
        minmax(
          0,
          1fr
        )
      );
  }

  .section-grid,
  .three-columns {
    grid-template-columns:
      1fr;
  }
}

@media (
  max-width:
  860px
) {
  .admin-sidebar {
    position:
      fixed;

    left:
      0;

    bottom:
      0;

    transform:
      translateX(
        -105%
      );

    transition:
      transform
      .22s ease;

    box-shadow:
      22px
      0
      70px
      rgba(
        0,
        0,
        0,
        .3
      );
  }

  .admin-sidebar.open {
    transform:
      translateX(
        0
      );
  }

  .sidebar-backdrop {
    display:
      block;

    position:
      fixed;

    inset:
      0;

    z-index:
      40;

    background:
      rgba(
        0,
        0,
        0,
        .35
      );

    backdrop-filter:
      blur(2px);
  }

  .mobile-admin-menu {
    width:
      38px;

    height:
      38px;

    display:
      grid;

    place-items:
      center;

    border:
      1px solid
      rgba(
        255,
        255,
        255,
        .08
      );

    border-radius:
      10px;

    background:
      rgba(
        255,
        255,
        255,
        .035
      );

    color:
      #dfe8f6;

    cursor:
      pointer;
  }

  .admin-topbar {
    padding:
      0
      16px;
  }

  .admin-content {
    padding:
      22px
      15px
      40px;
  }

  .page-heading {
    align-items:
      flex-start;

    flex-direction:
      column;
  }

  .heading-actions {
    width:
      100%;
  }
}

@media (
  max-width:
  760px
) {
  .cost-breakdown {
    grid-template-columns:
      1fr;
  }

  .model-meta-grid {
    grid-template-columns:
      1fr;
  }
}

@media (
  max-width:
  620px
) {
  .stats-grid {
    grid-template-columns:
      1fr;
  }

  .page-heading h1 {
    font-size:
      27px;
  }

  .heading-actions,
  .table-toolbar,
  .toolbar-actions {
    width:
      100%;
  }

  .heading-actions
  .ghost-button,
  .heading-actions
  .primary-button {
    flex:
      1;
  }

  .toolbar-actions
  .ghost-button,
  .toolbar-actions
  .primary-button {
    flex:
      1;
  }

  .sync-label {
    display:
      none;
  }

  .refresh-button {
    padding:
      0
      11px;
  }

  .admin-content {
    padding-left:
      11px;

    padding-right:
      11px;
  }

  .panel {
    padding:
      14px;
  }

  .chart-container {
    height:
      260px;
  }
}

/* =========================
   FONT SCALE
========================= */

.admin-app {
  font-size:
    16px;
}

.admin-brand strong {
  font-size:
    17px !important;
}

.admin-brand small {
  font-size:
    12px !important;
}

.nav-button {
  font-size:
    15px !important;
}

.nav-copy strong {
  font-size:
    14px !important;
}

.nav-copy small {
  font-size:
    12px !important;
}

.account-copy strong {
  font-size:
    14px !important;
}

.account-copy small {
  font-size:
    12px !important;
}

.admin-topbar {
  font-size:
    15px !important;
}

.topbar-title strong {
  font-size:
    18px !important;
}

.sync-label {
  font-size:
    12px !important;
}

.page-heading h1 {
  font-size:
    35px !important;
}

.page-heading p {
  font-size:
    15px !important;
}

.stat-content >
span {
  font-size:
    13px !important;
}

.stat-content >
strong {
  font-size:
    28px !important;
}

.stat-content >
small {
  font-size:
    11px !important;
}

.panel-head h2 {
  font-size:
    18px !important;
}

.panel-head small {
  font-size:
    12px !important;
}

.search-field input {
  font-size:
    14px !important;
}

th,
td {
  font-size:
    12px !important;
}

th {
  font-size:
    11px !important;
}

.user-cell strong {
  font-size:
    12px !important;
}

.user-cell small {
  font-size:
    11px !important;
}

.metric-item strong {
  font-size:
    12px !important;
}

.metric-item small {
  font-size:
    11px !important;
}

.metric-item b {
  font-size:
    13px !important;
}

.empty-state strong {
  font-size:
    14px !important;
}

.empty-state small {
  font-size:
    12px !important;
}

.modal-header h2 {
  font-size:
    22px !important;
}

.modal-form label {
  font-size:
    12px !important;
}

.login-box h1 {
  font-size:
    30px !important;
}

.login-box p {
  font-size:
    14px !important;
}

.login-form label {
  font-size:
    12px !important;
}

@media (
  max-width:
  620px
) {
  .page-heading h1 {
    font-size:
      30px !important;
  }

  .stat-content >
  strong {
    font-size:
      25px !important;
  }
}
</style>

<!-- 與前台一致的低彩度炭灰主題。放在最後覆蓋舊的藍色視覺，不影響功能。 -->
<style scoped>
:global(body) {
  background: #202123;
  color: #ececf1;
}

.admin-app,
.admin-login,
.login-shell {
  color: #ececf1;
  background: #202123;
}

.admin-app {
  --admin-bg: #202123;
  --admin-sidebar: #171717;
  --admin-surface: #262626;
  --admin-surface-2: #2b2b2b;
  --admin-border: #3b3b3f;
  --admin-border-soft: rgba(255, 255, 255, .07);
  --admin-text: #ececf1;
  --admin-muted: #96969e;
  --admin-accent: #10a37f;
  background: var(--admin-bg);
}

.admin-sidebar {
  background: var(--admin-sidebar);
  border-right-color: var(--admin-border-soft);
  backdrop-filter: none;
}

.admin-brand {
  border-bottom: 1px solid var(--admin-border-soft);
  margin-bottom: 10px;
  padding-bottom: 18px;
}

.brand-icon,
.brand-mark,
.login-mark {
  border-radius: 10px;
  background: #303033;
  color: #f1f1f3;
  box-shadow: none;
}

.brand-icon {
  position: relative;
  border: 1px solid #424246;
}

.brand-icon::after {
  content: '';
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: 8px;
  height: 8px;
  border: 2px solid var(--admin-sidebar);
  border-radius: 50%;
  background: var(--admin-accent);
}

.brand-copy small,
.nav-caption,
.nav-copy small,
.account-copy small,
.page-heading p,
.panel-head small,
.stat-content > small,
.metric-item small,
.model-meta-grid span,
.empty-state,
.empty-state small {
  color: var(--admin-muted);
}

.nav-caption,
.eyebrow,
.topbar-kicker {
  color: #7d7d85;
  letter-spacing: .11em;
}

.nav-button {
  min-height: 58px;
  border-radius: 10px;
  color: #b7b7bd;
}

.nav-button:hover {
  color: var(--admin-text);
  background: rgba(255, 255, 255, .045);
}

.nav-button.active {
  border-color: #414146;
  background: #2a2a2d;
  color: #fff;
}

.nav-icon,
.nav-button.active .nav-icon {
  border: 1px solid rgba(255, 255, 255, .06);
  border-radius: 9px;
  background: #323235;
  color: #d2d2d6;
}

.admin-account {
  border-color: var(--admin-border-soft);
  border-radius: 10px;
  background: #202022;
}

.account-avatar,
.table-avatar {
  background: #36363a;
  color: #f0f0f2;
}

.logout-admin {
  border-color: #414146;
  border-radius: 9px;
  background: transparent;
  color: #a9a9af;
}

.logout-admin:hover {
  background: rgba(255, 255, 255, .045);
  color: #f2f2f4;
}

.admin-main {
  background: #212121;
}

.admin-topbar {
  height: 64px;
  padding: 0 24px;
  border-bottom-color: var(--admin-border-soft);
  background: rgba(33, 33, 33, .92);
  backdrop-filter: blur(14px);
}

.topbar-actions {
  gap: 12px;
}

.topbar-date {
  padding-right: 12px;
  border-right: 1px solid var(--admin-border-soft);
  color: #85858d;
  font-size: 11px;
  white-space: nowrap;
}

.sync-label {
  color: #a0a0a7;
}

.status-dot,
.toast-dot {
  background: var(--admin-accent, #10a37f);
  box-shadow: none;
}

.admin-content {
  padding-top: 26px;
}

.page-heading {
  padding-bottom: 20px;
  border-bottom: 1px solid var(--admin-border-soft);
}

.page-heading h1 {
  color: #f0f0f2;
  letter-spacing: -.025em;
}

.refresh-button,
.ghost-button,
.collapse-button,
.actions button,
.table-pagination button {
  border-color: #414146;
  border-radius: 9px;
  background: #29292c;
  color: #cacacf;
  box-shadow: none;
}

.refresh-button:hover,
.ghost-button:hover,
.collapse-button:hover,
.actions button:hover,
.table-pagination button:hover:not(:disabled) {
  border-color: #505056;
  background: #333337;
  color: #fff;
}

.primary-button,
.login-button {
  border: 1px solid #3b3b3f;
  border-radius: 9px;
  background: #ececf1;
  color: #171719;
  box-shadow: none;
}

.primary-button:hover,
.login-button:hover:not(:disabled) {
  filter: none;
  background: #fff;
  transform: none;
}

.stats-grid {
  gap: 10px;
}

.stat-card,
.panel {
  border: 1px solid var(--admin-border-soft);
  border-radius: 12px;
  background: var(--admin-surface);
  box-shadow: 0 8px 24px rgba(0, 0, 0, .12);
}

.stat-card {
  min-height: 104px;
  padding: 16px;
}

.stat-card:hover,
.panel:hover {
  border-color: rgba(255, 255, 255, .11);
}

.stat-icon,
.stat-icon.blue,
.stat-icon.violet,
.stat-icon.amber {
  border: 1px solid #414146;
  border-radius: 9px;
  background: #303033;
  color: #c8c8cd;
}

.stat-content > span {
  color: #a5a5ac;
}

.stat-content > strong,
.cost-breakdown strong,
.metric-item b,
.model-meta-grid strong,
.reasoning-value,
.reasoning-cost {
  color: #f0f0f2;
  font-variant-numeric: tabular-nums;
}

.cost-breakdown > div,
.model-item,
.metric-item,
.model-meta-grid > div {
  border-color: var(--admin-border-soft);
  border-radius: 9px;
  background: #2b2b2e;
}

.rank {
  border: 1px solid #444449;
  background: #343438;
  color: #d1d1d5;
}

.progress {
  background: #3b3b3f;
}

.progress span {
  background: #9c9ca3;
}

.search-field,
.field,
.modal-form input {
  border-color: #45454a;
  border-radius: 9px;
  background: #2b2b2e;
  color: #f0f0f2;
}

.search-field.focused,
.field:focus-within,
.modal-form input:focus {
  border-color: #717178;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, .055);
}

.search-field input,
.field input,
.modal-form input {
  color: #f0f0f2;
}

.table-scroll {
  border-color: var(--admin-border-soft);
  border-radius: 10px;
  background: #242426;
}

th {
  background: #2b2b2e;
  color: #9c9ca4;
  text-transform: none;
  letter-spacing: .02em;
}

th,
td {
  border-bottom-color: var(--admin-border-soft);
  color: #c4c4ca;
  font-variant-numeric: tabular-nums;
}

tbody tr:nth-child(even) {
  background: rgba(255, 255, 255, .012);
}

tbody tr:hover {
  background: rgba(255, 255, 255, .035);
}

.user-cell strong,
.metric-item strong {
  color: #ececf1;
}

.badge,
.tag {
  border-color: #444449;
  background: #303034;
  color: #c5c5ca;
}

.modal {
  background: rgba(10, 10, 11, .72);
  backdrop-filter: blur(5px);
}

.modal-box {
  border-color: #444449;
  border-radius: 13px;
  background: #262628;
  box-shadow: 0 24px 70px rgba(0, 0, 0, .38);
}

.toast {
  border-color: #45454a;
  background: #2b2b2e;
  color: #f0f0f2;
  box-shadow: 0 14px 38px rgba(0, 0, 0, .3);
}

.login-shell {
  background: #202123;
}

.login-glow {
  display: none;
}

.login-box {
  border-color: #414146;
  border-radius: 14px;
  background: #262628;
  backdrop-filter: none;
  box-shadow: 0 22px 65px rgba(0, 0, 0, .3);
}

.login-box p,
.login-form label,
.login-note {
  color: #96969e;
}

.boot-spinner {
  border-color: #444449;
  border-top-color: #d7d7db;
}

:global(button:focus-visible),
:global(input:focus-visible) {
  outline: 2px solid rgba(236, 236, 241, .72);
  outline-offset: 2px;
}

.admin-app::-webkit-scrollbar,
.admin-app *::-webkit-scrollbar {
  width: 9px;
  height: 9px;
}

.admin-app::-webkit-scrollbar-thumb,
.admin-app *::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 999px;
  background: #505056;
  background-clip: padding-box;
}

@media (max-width: 720px), (pointer: coarse) {
  .topbar-date,
  .sync-label {
    display: none;
  }

  .admin-topbar {
    height: 60px;
    padding: 0 12px;
  }

  .admin-content {
    padding: 18px 12px 36px;
  }

  .page-heading {
    align-items: flex-start;
  }

  .stat-card,
  .panel {
    border-radius: 11px;
  }
}
</style>

<style scoped>
.admin-boot {
  min-height:
    100vh;
}

.boot-box {
  text-align:
    center;
}

.boot-spinner {
  width:
    32px;

  height:
    32px;

  margin:
    22px
    auto
    0;

  border:
    3px solid
    rgba(
      255,
      255,
      255,
      .13
    );

  border-top-color:
    currentColor;

  border-radius:
    50%;

  animation:
    ggpt-admin-spin
    .7s
    linear
    infinite;
}

@keyframes ggpt-admin-spin {
  to {
    transform:
      rotate(
        360deg
      );
  }
}
</style>
