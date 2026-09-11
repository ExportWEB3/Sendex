import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3001,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8090',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            const location = proxyRes.headers['location'];
            if (location && location.includes('localhost:8090')) {
              proxyRes.headers['location'] = location.replace('http://localhost:8090', '');
            }
          });
        }
      },
      '/health': {
        target: 'http://localhost:8090',
        changeOrigin: true,
      },
      '/t/': {
        target: 'http://localhost:8090',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
  }
})
