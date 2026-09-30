// Standard USD / 1M tokens. Source: developers.openai.com/api/docs/models
export const MODEL_OPTIONS = [
  { id: 'gpt-6.1-sol', label: 'GPT-6.1 Sol', desc: '複雜推理與程式設計', pricing: '$2 / $0.10 / $10', efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
  { id: 'gpt-6-sol', label: 'GPT-6 Sol', desc: '兼顧能力與成本', pricing: '$2 / $0.20 / $10', efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'] },
  { id: 'gpt-6-luna', label: 'GPT-6 Luna', desc: '快速、輕量的日常助手', pricing: '$0.10 / $0.01 / $0.50', efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'] },
  { id: 'gpt-6-astra', label: 'GPT-6 Astra', desc: '高階通用模型', pricing: '$10 / $1 / $50', efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
  { id: 'gpt-5.6-sol', label: 'GPT-5.6 Sol', desc: '通用對話模型', pricing: '$4 / $0.40 / $20', efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'] },
  { id: 'gpt-5.6-terra', label: 'GPT-5.6 Terra', desc: '推理與程式能力模型', pricing: '$2 / $0.20 / $12', efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'] },
  { id: 'gpt-5.6-luna', label: 'GPT-5.6 Luna', desc: '通用 AI 助手模型', pricing: '$0.20 / $0.02 / $1.20', efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'] },
]

export const DEFAULT_MODEL = 'gpt-6-luna'
export const REASONING_MODELS = new Set(MODEL_OPTIONS.map(item => item.id))
export const MODEL_REASONING_EFFORTS = Object.fromEntries(MODEL_OPTIONS.map(item => [item.id, item.efforts]))
