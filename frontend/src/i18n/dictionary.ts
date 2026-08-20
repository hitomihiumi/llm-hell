import type { Locale } from "./config";

/**
 * Every string the interface shows, in both locales.
 *
 * Stored as `Record<Locale, Copy>` the way the site stores its navigation and
 * case studies, rather than as flat keys looked up at runtime: the type makes
 * a missing Ukrainian string a compile error instead of a key rendered raw in
 * front of a visitor.
 *
 * Plural entries are arrays read by `pluralise` — two forms for English,
 * three for Ukrainian.
 *
 * Source names, model ids, file paths, SQL and error text from a backend stay
 * untranslated: they are data, and a GitLab error rendered in Ukrainian is
 * both wrong and unsearchable.
 */
export interface Copy {
  nav: {
    search: string;
    chat: string;
    sources: string;
    signOut: string;
    admin: string;
    layoutLabel: string;
    primaryLabel: string;
  };
  login: {
    eyebrow: string;
    title: string;
    lede: string;
    username: string;
    password: string;
    submit: string;
    submitting: string;
    wrongCredentials: string;
    backendDown: string;
  };
  search: {
    eyebrow: string;
    title: string;
    placeholder: string;
    submit: string;
    searching: string;
    sources: string;
    generateAnswer: string;
    noResults: string;
    results: string[];
    sourcesError: string;
    switchedOff: string;
  };
  chat: {
    eyebrow: string;
    title: string;
    lede: string;
    suggestions: string[];
    placeholder: string;
    allSources: string;
    someSources: (selected: number, total: number) => string;
    stop: string;
    send: string;
    askLabel: string;
  };
  answer: {
    title: string;
    writing: string;
    fromOf: (used: number, total: number) => string;
    showReasoning: string;
    hideReasoning: string;
    loadingLabel: string;
  };
  message: {
    sources: string[];
    answeredFrom: (used: number) => string;
    unavailable: (count: number) => string;
    searchingLabel: string;
  };
  card: {
    view: string;
    showText: string;
    hideText: string;
    kinds: Record<string, string>;
  };
  badge: {
    hits: string[];
    unavailable: string;
    partial: string;
    fallback: string;
    fallbackTitle: string;
    generatedSql: string;
  };
  viewer: {
    pages: (count: number) => string;
    eyebrow: string;
    close: string;
    truncated: string;
    nothing: string;
    loading: string;
  };
  record: {
    back: string;
    notFound: string;
    unreachable: string;
  };
  sources: {
    eyebrow: string;
    title: string;
    lede: string;
    enable: string;
    disable: string;
    check: string;
    checking: string;
    reachable: string;
    unreachable: string;
    checkedAt: string;
    toolsExposed: (count: number) => string;
    weight: string;
    enabled: string;
    disabled: string;
    adminOnlyCheck: string;
    adminOnlyToggle: string;
    checkFailed: string;
    updateFailed: string;
    loadFailed: string;
  };
}

const en: Copy = {
  nav: {
    search: "Search",
    chat: "Chat",
    sources: "Sources",
    signOut: "Sign out",
    admin: "admin",
    layoutLabel: "Layout",
    primaryLabel: "Primary",
  },
  login: {
    eyebrow: "Knowledge base",
    title: "Sign in",
    lede: "Search Google Workspace, GitLab and the internal knowledge base at once — with every answer cited back to its source.",
    username: "Username",
    password: "Password",
    submit: "Sign in",
    submitting: "Signing in",
    wrongCredentials: "Incorrect username or password.",
    backendDown: "Could not sign in. Is the backend running?",
  },
  search: {
    eyebrow: "Federated search",
    title: "Ask the knowledge base",
    placeholder: "Ask a question, or search for a term",
    submit: "Search",
    searching: "Searching",
    sources: "Sources",
    generateAnswer: "Generate an answer",
    noResults: "No results.",
    results: ["Result", "Results"],
    sourcesError: "Could not load the source list.",
    switchedOff: "Switched off by an admin",
  },
  chat: {
    eyebrow: "Knowledge base",
    title: "Ask anything",
    lede: "Answers come from your Drive, GitLab and internal records — with the sources attached.",
    suggestions: [
      "How does result ranking work?",
      "Why must the KV cache be fp8?",
      "What breaks when the open-file limit is too low?",
    ],
    placeholder: "Ask a question…  (Enter to send, Shift+Enter for a new line)",
    allSources: "All sources",
    someSources: (selected, total) => `${selected} of ${total} sources`,
    stop: "Stop",
    send: "Send",
    askLabel: "Ask a question",
  },
  answer: {
    title: "Answer",
    writing: "Writing",
    fromOf: (used, total) => `from ${used} of ${total} results`,
    showReasoning: "+ Show reasoning",
    hideReasoning: "− Hide reasoning",
    loadingLabel: "Writing the answer",
  },
  message: {
    sources: ["source", "sources"],
    answeredFrom: (used) => ` · answered from ${used}`,
    unavailable: (count) => ` · ${count} unavailable`,
    searchingLabel: "Searching",
  },
  card: {
    view: "View",
    showText: "+ Show what was read",
    hideText: "− Hide what was read",
    kinds: {
      document: "Doc",
      email: "Email",
      code: "Code",
      repository: "Repo",
      commit: "Commit",
      row: "Record",
      unknown: "Item",
    },
  },
  badge: {
    hits: ["hit", "hits"],
    unavailable: "unavailable",
    partial: "partial",
    fallback: "fallback",
    fallbackTitle:
      "The model did not produce usable SQL, so a deterministic keyword query was used instead.",
    generatedSql: "Generated SQL",
  },
  viewer: {
    pages: (count) => `${count} page${count === 1 ? "" : "s"} the answer read`,
    eyebrow: "Source",
    close: "Close",
    truncated: "Truncated — open the source for the rest",
    nothing: "This result has nothing more to show.",
    loading: "Loading",
  },
  record: {
    back: "Back to search",
    notFound: "That record does not exist, or its table is not searchable.",
    unreachable: "The knowledge base could not be reached.",
  },
  sources: {
    eyebrow: "Configuration",
    title: "Sources",
    lede: "What a source can actually do is not knowable from configuration — whether a GitLab instance supports code search, for instance. Run a check to ask its server directly.",
    enable: "Enable",
    disable: "Disable",
    check: "Check",
    checking: "Checking",
    reachable: "reachable",
    unreachable: "unreachable",
    checkedAt: "checked",
    toolsExposed: (count) => `${count} tools exposed`,
    weight: "weight",
    enabled: "enabled",
    disabled: "disabled",
    adminOnlyCheck: "Checking a source requires an admin account.",
    adminOnlyToggle: "Changing a source requires an admin account.",
    checkFailed: "The check failed.",
    updateFailed: "The update failed.",
    loadFailed: "Could not load sources.",
  },
};

const uk: Copy = {
  nav: {
    search: "Пошук",
    chat: "Чат",
    sources: "Джерела",
    signOut: "Вийти",
    admin: "адмін",
    layoutLabel: "Вигляд",
    primaryLabel: "Основна навігація",
  },
  login: {
    eyebrow: "База знань",
    title: "Вхід",
    lede: "Шукайте в Google Workspace, GitLab і внутрішній базі знань одночасно — кожна відповідь із посиланням на джерело.",
    username: "Ім'я користувача",
    password: "Пароль",
    submit: "Увійти",
    submitting: "Входимо",
    wrongCredentials: "Невірне ім'я користувача або пароль.",
    backendDown: "Не вдалося увійти. Бек-енд запущений?",
  },
  search: {
    eyebrow: "Федеративний пошук",
    title: "Запитайте базу знань",
    placeholder: "Поставте питання або введіть термін",
    submit: "Шукати",
    searching: "Шукаємо",
    sources: "Джерела",
    generateAnswer: "Згенерувати відповідь",
    noResults: "Нічого не знайдено.",
    results: ["Результат", "Результати", "Результатів"],
    sourcesError: "Не вдалося завантажити список джерел.",
    switchedOff: "Вимкнено адміністратором",
  },
  chat: {
    eyebrow: "База знань",
    title: "Запитайте будь-що",
    lede: "Відповіді збираються з вашого Drive, GitLab і внутрішніх записів — разом із джерелами.",
    suggestions: [
      "Як працює ранжування результатів?",
      "Чому KV-кеш має бути fp8?",
      "Що ламається, коли ліміт відкритих файлів замалий?",
    ],
    placeholder:
      "Поставте питання…  (Enter — надіслати, Shift+Enter — новий рядок)",
    allSources: "Усі джерела",
    someSources: (selected, total) => `${selected} з ${total} джерел`,
    stop: "Спинити",
    send: "Надіслати",
    askLabel: "Поставте питання",
  },
  answer: {
    title: "Відповідь",
    writing: "Пишемо",
    fromOf: (used, total) => `з ${used} із ${total} результатів`,
    showReasoning: "+ Показати міркування",
    hideReasoning: "− Сховати міркування",
    loadingLabel: "Пишемо відповідь",
  },
  message: {
    sources: ["джерело", "джерела", "джерел"],
    answeredFrom: (used) => ` · відповідь із ${used}`,
    unavailable: (count) => ` · ${count} недоступно`,
    searchingLabel: "Шукаємо",
  },
  card: {
    view: "Дивитись",
    showText: "+ Показати розпізнане",
    hideText: "− Сховати розпізнане",
    kinds: {
      document: "Док",
      email: "Лист",
      code: "Код",
      repository: "Репо",
      commit: "Коміт",
      row: "Запис",
      unknown: "Об'єкт",
    },
  },
  badge: {
    hits: ["збіг", "збіги", "збігів"],
    unavailable: "недоступне",
    partial: "частково",
    fallback: "запасний",
    fallbackTitle:
      "Модель не видала придатного SQL, тому виконано детермінований пошук за ключовими словами.",
    generatedSql: "Згенерований SQL",
  },
  viewer: {
    pages: (count) => `${count} сторінок, які читала відповідь`,
    eyebrow: "Джерело",
    close: "Закрити",
    truncated: "Обрізано — відкрийте джерело, щоб побачити решту",
    nothing: "Для цього результату більше нічого показати.",
    loading: "Завантаження",
  },
  record: {
    back: "Назад до пошуку",
    notFound: "Такого запису немає, або його таблиця не входить у пошук.",
    unreachable: "Не вдалося звернутися до бази знань.",
  },
  sources: {
    eyebrow: "Конфігурація",
    title: "Джерела",
    lede: "Що джерело справді вміє, з конфігурації не видно — чи підтримує цей GitLab пошук по коду, наприклад. Запустіть перевірку, щоб спитати сам сервер.",
    enable: "Увімкнути",
    disable: "Вимкнути",
    check: "Перевірити",
    checking: "Перевіряємо",
    reachable: "доступне",
    unreachable: "недоступне",
    checkedAt: "перевірено",
    toolsExposed: (count) => `${count} інструментів доступно`,
    weight: "вага",
    enabled: "увімкнено",
    disabled: "вимкнено",
    adminOnlyCheck:
      "Перевірка джерела потребує облікового запису адміністратора.",
    adminOnlyToggle: "Зміна джерела потребує облікового запису адміністратора.",
    checkFailed: "Перевірка не вдалася.",
    updateFailed: "Не вдалося оновити.",
    loadFailed: "Не вдалося завантажити джерела.",
  },
};

export const DICTIONARY: Record<Locale, Copy> = { en, uk };
