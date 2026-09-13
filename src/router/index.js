import {
  createRouter,
  createWebHistory
} from 'vue-router'


const ChatView = () =>
  import('../views/ChatView.vue')

const AdminView = () =>
  import('../views/AdminView.vue')

const routes = [
  {
    path: '/',
    name: 'chat',
    component: ChatView
  },

  {
    path: '/c/:shareToken',
    name: 'shared-conversation',
    component: ChatView
  },

  {
    path: '/admin',
    name: 'admin',
    component: AdminView
  },

  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    redirect: '/'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router