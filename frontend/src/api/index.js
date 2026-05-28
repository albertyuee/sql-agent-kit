import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

export const queryApi = {
  run: (question) => http.post('/query', { question }),
  stream: (question) =>
    new EventSource(`/api/query/stream?question=${encodeURIComponent(question)}`),
}

export const analysisApi = {
  // Returns a native EventSource — caller manages lifecycle
  stream: (question) =>
    new EventSource(`/api/analysis/stream?question=${encodeURIComponent(question)}`),
}

export const historyApi = {
  list: (params) => http.get('/history', { params }),
  deleteOne: (index, keyword = '') =>
    http.delete(`/history/${index}`, { params: keyword ? { keyword } : {} }),
  clearAll: () => http.delete('/history'),
}

export const configApi = {
  get: () => http.get('/config'),
  save: (payload) => http.post('/config', payload),
  testLLM: (payload) => http.post('/config/test-llm', payload),
  testDB: (payload) => http.post('/config/test-db', payload),
}

export const tablesApi = {
  get: () => http.get('/tables'),
  save: (tables) => http.post('/tables', { tables }),
}

export const annotationsApi = {
  get: () => http.get('/annotations'),
  save: (content) => http.post('/annotations', { content }),
  generate: () => http.post('/annotations/generate', {}, { timeout: 120000 }),
}

export const paramsApi = {
  get: () => http.get('/params'),
  save: (payload) => http.post('/params', payload),
}

export const fewshotApi = {
  list: () => http.get('/fewshot'),
  add: (question, sql) => http.post('/fewshot', { question, sql }),
  delete: (index) => http.delete(`/fewshot/${index}`),
}

export const suggestionsApi = {
  get: () => http.get('/suggestions'),
}

export const runSqlApi = {
  run: (sql, intent = '') => http.post('/run-sql', { sql, intent }),
}