// i18n（F14 键位）：中文为主 + EN 键同步维护（UI 规范 §8.2）
const DICT = {
  zh: {
    'nav.battle': '对战', 'nav.collection': '卡组与卡库',
    'zone.front': '前台', 'zone.admin': '后台',
    'btn.login': '登录 / 注册', 'btn.logout': '退出', 'btn.start': '开始对局',
    'btn.restart': '重开对局', 'btn.submit': '提交任务', 'btn.cancel': '取消（保留已完成）',
    'btn.export': '导出', 'btn.retry': '重试', 'btn.parse': '解析并校验',
    'btn.copy_seed': '复制 seed', 'btn.opening': '起手 1,000 次模拟',
    'lbl.deck_mine': '我方卡组', 'lbl.deck_opp': '对手卡组', 'lbl.games': '局数',
    'lbl.seed': '起始 seed', 'lbl.ai_iters': 'AI 强度（0=规则基线；>0=MCTS 迭代）',
    'empty.battle': '暂无对局',
    'empty.tasks': '暂无批量任务',
    'empty.decks': '暂无卡组',
    'state.loading': '加载中…', 'state.thinking': '对方思考中…',
    'tip.legal': '合法动作',
    'tip.unofficial': 'Unofficial community data · 非官方数据',
    'confirm.cancel_task': '取消将停止后续调度，已完成的对局会保留。确认取消？',
  },
  en: {
    'nav.battle': 'Battle', 'nav.collection': 'Decks & Library',
    'zone.front': 'Front', 'zone.admin': 'Admin',
    'btn.login': 'Sign in / Register', 'btn.logout': 'Sign out',
    'btn.start': 'Start match', 'btn.restart': 'Restart', 'btn.submit': 'Submit',
    'btn.cancel': 'Cancel (keep partial)', 'btn.export': 'Export',
    'btn.retry': 'Retry', 'btn.parse': 'Parse & validate',
    'btn.copy_seed': 'Copy seed', 'btn.opening': 'Opening ×1,000',
    'lbl.deck_mine': 'My deck', 'lbl.deck_opp': 'Opponent deck',
    'lbl.games': 'Games', 'lbl.seed': 'Seed',
    'lbl.ai_iters': 'AI strength (0 = rule baseline; >0 = MCTS iterations)',
    'empty.battle': 'No match yet',
    'empty.tasks': 'No batch jobs yet',
    'empty.decks': 'No decks yet',
    'state.loading': 'Loading…', 'state.thinking': 'Opponent is thinking…',
    'tip.legal': 'Legal actions',
    'tip.unofficial': 'Unofficial community data',
    'confirm.cancel_task': 'Cancel stops scheduling; finished games are kept. OK?',
  },
}
export const lang = (typeof localStorage !== 'undefined'
  && localStorage.getItem('pokelab_lang')) || 'zh'
export function t(key) {
  return DICT[lang]?.[key] ?? DICT.zh[key] ?? key
}
