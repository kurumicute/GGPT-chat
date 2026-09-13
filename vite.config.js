import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = path.dirname(
  fileURLToPath(import.meta.url)
)

const backend = 'http://127.0.0.1:8080'

const proxyOptions = {
  target: backend,
  changeOrigin: true,
}

export default defineConfig(async () => {
  const plugins = [vue()]

  // 使用 ANALYZE=true 建置時產生 Bundle Analyzer 報告
  if (process.env.ANALYZE === 'true') {
    try {
      const { visualizer } = await import(
        'rollup-plugin-visualizer'
      )

      plugins.push(
        visualizer({
          filename: 'dist/bundle-report.html',
          template: 'treemap',
          gzipSize: true,
          brotliSize: true,
          open: false,
        })
      )
    } catch {
      console.warn(
        '請先安裝 rollup-plugin-visualizer 才能產生分析報告。'
      )
    }
  }

  return {
    plugins,

    /*
     * 使用全新的 Vite 相依快取位置。
     * 避免網域端再次載入舊的 node_modules/.vite/deps。
     */
    cacheDir: 'node_modules/.vite-chat-domain-v1',

    /*
     * 強制所有程式使用同一份 Vue。
     * 避免 Vue Router 的 provide/inject Symbol 不一致。
     */
    resolve: {
      alias: [
        {
          find: /^vue$/,
          replacement: path.resolve(
            projectRoot,
            'node_modules/vue/dist/vue.esm-bundler.js'
          ),
        },
      ],

      dedupe: [
        'vue',
        'vue-router',
        'pinia',
        '@vue/compiler-core',
        '@vue/compiler-dom',
        '@vue/compiler-sfc',
        '@vue/reactivity',
        '@vue/runtime-core',
        '@vue/runtime-dom',
        '@vue/shared',
      ],
    },

    /*
     * 強制重新預先打包 Vue、Vue Router 和 Pinia。
     */
    optimizeDeps: {
      include: [
        'vue',
        'vue-router',
        'pinia',
      ],
      force: true,
    },

    build: {
      target: 'es2020',
      cssCodeSplit: true,
      reportCompressedSize: true,
    },

    server: {
      host: true,
      port: 5173,

      /*
       * 公開網域暫時停用 HMR。
       * 避免舊 Vue 模組留在瀏覽器記憶體中。
       */
      hmr: false,

      /*
       * 禁止 Cloudflare 和瀏覽器快取開發模組。
       */
      headers: {
        'Cache-Control':
          'no-store, no-cache, must-revalidate, proxy-revalidate',
        Pragma: 'no-cache',
        Expires: '0',
      },

      allowedHosts: [
        'chat.kurumicute.com',
      ],

      proxy: {
        // 登入與 Session
        '/login': proxyOptions,
        '/register': proxyOptions,
        '/logout': proxyOptions,
        '/check_session': proxyOptions,

        // 聊天
        '/chat': proxyOptions,
        '/usage': proxyOptions,

        // 對話與分享
        '/conversations': proxyOptions,
        '/shared-conversations': proxyOptions,

        // 檔案上傳
        '/upload': proxyOptions,
        '/upload_image': proxyOptions,
        '/upload_file': proxyOptions,
        '/uploads': proxyOptions,

        // 其他 API
        '/auth': proxyOptions,
        '/tts': proxyOptions,
        '/api': proxyOptions,
      },
    },
  }
})