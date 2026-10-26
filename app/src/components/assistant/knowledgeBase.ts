/**
 * Kinjy Assistant — Knowledge Base
 * Grounded in the Kinjy blueprint (info.md). Every entry is tagged by
 * role and module, carries a citation source + KB version, and ships localized
 * answer templates in English, Kiswahili, Français, العربية and 中文.
 * The agent replies in the same language the user wrote in (detected per
 * message, falling back to the active i18n UI language).
 */

export type Lang = 'en' | 'sw' | 'fr' | 'ar' | 'zh'
export type Role = 'visitor' | 'member' | 'admin'

export const LANG_META: Record<Lang, { label: string; dir: 'ltr' | 'rtl' }> = {
  en: { label: 'English', dir: 'ltr' },
  sw: { label: 'Kiswahili', dir: 'ltr' },
  fr: { label: 'Français', dir: 'ltr' },
  ar: { label: 'العربية', dir: 'rtl' },
  zh: { label: '中文', dir: 'ltr' },
}

export interface KBEntry {
  id: string
  /** Localized short title — used for suggestion chips and citations. */
  title: Record<Lang, string>
  /** Citation module, e.g. "Feeds guide". */
  module: string
  /** KB version this answer was last refreshed at. */
  version: string
  /** Roles allowed to see this answer. */
  roles: Role[]
  /** Admin-only knowledge — refused/escalated for other roles. */
  adminOnly?: boolean
  /** Lowercase keywords across all five languages for intent scoring. */
  keywords: string[]
  /** Localized written answer (plain sentences; also used as video captions). */
  answer: Record<Lang, string>
  /** Optional numbered steps (English callouts for the written tab). */
  steps?: string[]
  /** Illustrative image embedded in the written answer. */
  image?: string
  imageAlt?: string
  /** In-app deep link chip. */
  deepLink?: { to: string; label: string }
}

export const KB_VERSION = 'v2.17.0'
export const KB_LAST_SYNC = 'just now'

/** Simulated changelog-ingestion log (the self-update pipeline, assistant.md §5). */
export const INGEST_LOG = [
  `${KB_VERSION} — "Marketplace finality policy (2026-10-27); payments on Sonco Pay and Kaluta Pay only" → 4 answers updated, 20 locales regenerated`,
  'v2.16.0 — "Wave-2 feature coverage completed" → 17 answers added, 85 locales regenerated, evals passed',
  'v2.15.0 — "Payments guide shipped" → 6 answers added, 26 locales regenerated, evals passed',
  'v2.14.0 — "Kinjy Leaders fraud review step added" → 3 answers updated, 1 demo clip re-rendered',
  'v2.13.2 — "Side-by-side translation view shipped" → 2 answers updated, captions refreshed',
  'v2.13.1 — "City ad sponsorship floor from $5/day" → 1 answer updated, evals passed',
  'v2.13.0 — "Passkeys: hardware-key backup" → 1 answer updated, 4 locales regenerated',
]

/* ------------------------------------------------------------------ */
/* Entries                                                             */
/* ------------------------------------------------------------------ */

export const KB_ENTRIES: KBEntry[] = [
  {
    id: 'feed-modes',
    title: { en: 'Feed modes', sw: 'Njia za Mlisho', fr: 'Modes de fil', ar: 'أوضاع الخلاصة', zh: '信息流模式' },
    module: 'Feeds guide',
    version: 'v2.14.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['feed', 'feed mode', 'for you', 'following', 'trending', 'new mode', 'why am i seeing', 'mlisho', 'fil', 'fil d’actualité', 'الخلاصة', '信息流', 'لماذا أرى'],
    answer: {
      en: 'Kinjy replaces the single opaque newsfeed with 10 explicit feed modes: Following (strict reverse-chronological), For You (AI-personalized), Circles, Friends, Local, Country, Global, Topics, Trending and New. Switch modes with the gold chips above any feed. Every recommended post carries "Why am I seeing this?", "Show less like this" and "Change my algorithm" — your agency is first-class UI, not a footnote.',
      sw: 'Kinjy inabadilisha mlisho mmoja usioeleweka kuwa njia 10 wazi: Following (mpangilio wa kisasa), For You (ubinafsishaji wa AI), Circles, Friends, Local, Country, Global, Topics, Trending na New. Badilisha kwa vitambaa vya dhahabu juu ya mlisho wowote. Kila chapisho linalopendekezwa lina "Kwa nini naona hili?", "Onyesha machache kama haya" na "Badilisha algorithm yangu".',
      fr: 'Kinjy remplace le fil unique et opaque par 10 modes explicites : Following (chronologique strict), For You (personnalisé par IA), Circles, Friends, Local, Country, Global, Topics, Trending et New. Changez de mode via les pastilles dorées au-dessus du fil. Chaque publication recommandée affiche « Pourquoi je vois ceci ? », « Moins de contenu similaire » et « Changer mon algorithme ».',
      ar: 'تستبدل كالوتا الخلاصة الواحدة الغامضة بعشرة أوضاع صريحة: المتابَعون (زمني عكسي صارم)، لك (تخصيص بالذكاء الاصطناعي)، الدوائر، الأصدقاء، المحلي، البلد، العالمي، المواضيع، الرائج والجديد. بدّل الوضع من الرقائق الذهبية أعلى أي خلاصة. كل منشور مقترح يحمل "لماذا أرى هذا؟" و"أظهر أقل من هذا" و"غيّر الخوارزمية".',
      zh: 'Kinjy 用 10 种明确的信息流模式取代单一不透明动态：Following（严格时间倒序）、For You（AI 个性化）、Circles、Friends、Local、Country、Global、Topics、Trending 和 New。通过信息流上方的金色标签切换。每条推荐内容都带有"为什么我看到这个？"、"少看类似内容"和"更换我的算法"。',
    },
    steps: [
      'Open any feed and look at the gold mode chips on top.',
      'Tap a mode — e.g. Following for pure reverse-chronological order.',
      'On any recommended post, open "Why am I seeing this?" for the exact reason chips.',
    ],
    image: '/app-feed-mock.jpg',
    imageAlt: 'Kinjy feed in Cloud mode with gold feed-mode chips',
    deepLink: { to: '/feeds', label: 'Open Feeds' },
  },
  {
    id: 'algorithm-marketplace',
    title: { en: 'Algorithm Marketplace', sw: 'Soko la Algorithm', fr: 'Marché des algorithmes', ar: 'سوق الخوارزميات', zh: '算法市场' },
    module: 'Feeds guide',
    version: 'v2.14.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['algorithm', 'marketplace', 'chronological', 'family first', 'change my algorithm', 'ranking', 'algorithmi', 'algorithme', 'خوارزمية', '算法', 'تغيير الخوارزمية', 'badilisha algorithm'],
    answer: {
      en: 'You choose the ranking brain of your feed. The Algorithm Marketplace offers 15 user-selectable algorithms — Chronological, Friends First, Family First, Local News, Business, Technology, Entertainment, Learning, Politics, Positive Content, Long-form, Video Only, Audio Only, New Creators and Global Discovery. Developers can publish their own feed algorithms via API, and you can install them like apps.',
      sw: 'Wewe mwenyewe unachagua algorithm ya mlisho wako. Soko la Algorithm lina algorithm 15 za kuchagua — Chronological, Friends First, Family First, Local News, Business, Technology, Entertainment, Learning, Politics, Positive Content, Long-form, Video Only, Audio Only, New Creators na Global Discovery. Wasanidi wanaweza kuchapisha algorithm zao kupitia API.',
      fr: 'C’est vous qui choisissez le cerveau de votre fil. Le Marché des algorithmes propose 15 algorithmes sélectionnables — Chronological, Friends First, Family First, Local News, Business, Technology, Entertainment, Learning, Politics, Positive Content, Long-form, Video Only, Audio Only, New Creators et Global Discovery. Les développeurs peuvent publier leurs propres algorithmes via l’API.',
      ar: 'أنت من يختار عقل ترتيب خلاصتك. يوفر سوق الخوارزميات 15 خوارزمية قابلة للاختيار — الزمنية، الأصدقاء أولاً، العائلة أولاً، الأخبار المحلية، الأعمال، التقنية، الترفيه، التعلم، السياسة، المحتوى الإيجابي، الطويل، الفيديو فقط، الصوت فقط، المبدعون الجدد والاستكشاف العالمي. ويمكن للمطورين نشر خوارزمياتهم عبر واجهة البرمجة.',
      zh: '由你选择信息流的排序大脑。算法市场提供 15 种可选算法——时间顺序、好友优先、家人优先、本地新闻、商业、科技、娱乐、学习、时政、正能量、长文、仅视频、仅音频、新创作者和全球发现。开发者还可以通过 API 发布自己的信息流算法。',
    },
    steps: [
      'Tap the mode chips above your feed, then "Change my algorithm".',
      'Browse the Marketplace — each card states what it optimizes for and who published it.',
      'Install one (Family First is a lovely start) — the feed reorders instantly and reversibly.',
    ],
    image: '/assistant-illustration-1.jpg',
    imageAlt: 'How to change your feed algorithm — numbered gold callouts',
    deepLink: { to: '/feeds', label: 'Open Algorithm Marketplace' },
  },
  {
    id: 'circles',
    title: { en: 'Circles', sw: 'Duruni (Circles)', fr: 'Cercles', ar: 'الدوائر', zh: '圈子' },
    module: 'Circles guide',
    version: 'v2.12.1',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['circle', 'circles', 'close friends', 'private network', 'smart circle', 'duruni', 'cercle', 'دائرة', 'دوائر', '圈子'],
    answer: {
      en: 'Circles are your private, filtered networks — Family, Close Friends, Business, Customers, plus Smart Circles that maintain themselves from your interaction patterns (always visible and editable). Post to a circle and only that circle sees it; each circle can have its own feed mode and notification rules.',
      sw: 'Circles ni mitandao yako ya faragha — Family, Close Friends, Business, Customers, na Smart Circles zinazojitengeneza kutoka kwa mwingiliano wako (unaona na kuhariri kila wakati). Chapisho la duru moja huonekana na duru hiyo pekee.',
      fr: 'Les Cercles sont vos réseaux privés et filtrés — Famille, Amis proches, Affaires, Clients, plus des Smart Circles auto-entretenus selon vos interactions (toujours visibles et modifiables). Une publication dans un cercle n’est visible que par ce cercle.',
      ar: 'الدوائر هي شبكاتك الخاصة المصفّاة — العائلة، الأصدقاء المقربون، الأعمال، العملاء، إضافة إلى دوائر ذكية تُحدَّث من أنماط تفاعلك (مرئية وقابلة للتعديل دائمًا). ما تنشره في دائرة لا يراه إلا أعضاؤها.',
      zh: '圈子是你的私密筛选网络——家人、密友、商务、客户，以及根据你的互动模式自动维护的智能圈子（始终可见可编辑）。发布到某个圈子的内容仅该圈子可见。',
    },
    deepLink: { to: '/platform', label: 'See Circles' },
  },
  {
    id: 'communities',
    title: { en: 'Communities & Groups', sw: 'Jumuiya na Vikundi', fr: 'Communautés & Groupes', ar: 'المجتمعات والمجموعات', zh: '社区与群组' },
    module: 'Communities guide',
    version: 'v2.12.1',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['community', 'communities', 'group', 'groups', 'paid group', 'jumuiya', 'kikundi', 'communauté', 'groupe', 'مجتمع', 'مجموعة', '社区', '群组'],
    answer: {
      en: 'Communities are public, private, secret or paid groups built around any interest. Paid communities plug into the creator economy (the creator keeps their share, and the member’s sponsor earns 20% of what Kinjy retains), and every community gets AI Community Managers for summaries, duplicate detection and Q&A from its own knowledge.',
      sw: 'Jumuiya ni vikundi vya umma, faragha, siri au vya kulipia vinavyojengwa kuhusu maslahi yoyote. Jumuiya za kulipia huunganishwa na uchumi wa waundaji, na kila jumuiya ina Wasimamizi wa AI kwa muhtasari na maswali na majibu.',
      fr: 'Les communautés sont des groupes publics, privés, secrets ou payants autour de n’importe quel intérêt. Les communautés payantes s’intègrent à l’économie créateur, et chaque communauté dispose de gestionnaires IA pour résumés, détection de doublons et questions-réponses.',
      ar: 'المجتمعات هي مجموعات عامة أو خاصة أو سرية أو مدفوعة حول أي اهتمام. المجتمعات المدفوعة تندمج مع اقتصاد المبدعين، وكل مجتمع يحصل على مديري مجتمع بالذكاء الاصطناعي للتلخيص وكشف التكرار والإجابة عن الأسئلة.',
      zh: '社区是围绕任何兴趣建立的公开、私密、秘密或付费群组。付费社区接入创作者经济（创作者保留自己的份额，会员的推荐人可获得 Kinjy 留存部分的 20%），每个社区都有 AI 社区管理员负责摘要、查重和基于社区知识的问答。',
    },
    deepLink: { to: '/platform', label: 'See Communities' },
  },
  {
    id: 'forums',
    title: { en: 'Forums', sw: 'Majukwaa (Forums)', fr: 'Forums', ar: 'المنتديات', zh: '论坛' },
    module: 'Forums guide',
    version: 'v2.12.1',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['forum', 'forums', 'thread', 'discussion', 'topic hierarchy', 'jukwaa', 'foro', 'منتدى', 'منتديات', '论坛', '讨论'],
    answer: {
      en: 'Forums organize long-form discussion into geographic and topic hierarchies. AI Forum Assistants produce summaries, answer questions from forum knowledge with citations, detect duplicates, and Forum-to-Knowledge transformation turns great threads into a structured knowledge base.',
      sw: 'Forums hupanga mijadala mirefu kwa ngazi za kijiografia na mada. Wasaidizi wa AI hutoa muhtasari, kujibu maswali kutoka maarifa ya jukwaa kwa nukuu, na kugeuza mijadala kuwa hifadhidata ya maarifa iliyopangwa.',
      fr: 'Les forums organisent les discussions longues en hiérarchies géographiques et thématiques. Les assistants IA de forum produisent des résumés, répondent avec citations, détectent les doublons et transforment les fils en base de connaissances structurée.',
      ar: 'تنظم المنتديات النقاشات المطولة في تسلسلات جغرافية وموضوعية. مساعدو الذكاء الاصطناعي ينتجون الملخصات ويجيبون من معرفة المنتدى مع الاستشهادات ويكشفون التكرار، وتحويل المنتدى إلى معرفة يحوّل النقاشات إلى قاعدة معرفة منظمة.',
      zh: '论坛按地理和主题层级组织长篇讨论。AI 论坛助手生成摘要、基于论坛知识带引用地回答问题、检测重复，"论坛转知识"功能把优质讨论串转化为结构化知识库。',
    },
    deepLink: { to: '/platform', label: 'See Forums' },
  },
  {
    id: 'messenger',
    title: { en: 'Private Messenger', sw: 'Mjumbe wa Faragha', fr: 'Messagerie privée', ar: 'المراسل الخاص', zh: '私密消息' },
    module: 'Messenger guide',
    version: 'v2.13.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['message', 'messages', 'messenger', 'chat', 'call', 'voice call', 'video call', 'e2e', 'encrypted', 'ujumbe', 'messagerie', 'رسالة', 'محادثة', '消息', '聊天', '通话'],
    answer: {
      en: 'The Private Messenger offers end-to-end encrypted one-to-one and group chats with voice and video calls. Messages auto-translate across languages when both parties opt in, and translation is processed through the provider-independent Language Gateway — never stored as plaintext beyond your devices.',
      sw: 'Mjumbe wa Faragha una gumzo la mtu kwa mtu na vikundi vilivyosimbwa kwa njia fiche (E2E) pamoja na simu za sauti na video. Ujumbe hutafsiriwa kiotomatiki kati ya lugha pindi pande zote zinapokubali.',
      fr: 'La messagerie privée propose des discussions chiffrées de bout en bout, individuelles ou en groupe, avec appels audio et vidéo. Les messages se traduisent automatiquement dès que les deux parties l’activent.',
      ar: 'يوفر المراسل الخاص محادثات فردية وجماعية مشفرة طرفًا لطرف مع مكالمات صوتية ومرئية. تُترجم الرسائل تلقائيًا بين اللغات عندما يوافق الطرفان.',
      zh: '私密消息提供端到端加密的一对一和群组聊天，支持语音和视频通话。双方同意后消息可跨语言自动翻译，翻译经由独立于供应商的语言网关处理。',
    },
    deepLink: { to: '/platform', label: 'See Messenger' },
  },
  {
    id: 'creator-studio',
    title: { en: 'Creator Studio & One-to-Many', sw: 'Studio ya Mwandaji', fr: 'Studio Créateur', ar: 'استوديو المبدعين', zh: '创作者工作室' },
    module: 'Creator Studio guide',
    version: 'v2.14.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['creator', 'studio', 'one-to-many', 'publish', 'formats', 'newsletter', 'podcast', 'copilot', 'mwandaji', 'créateur', 'منشئ', 'استوديو', '创作者', '发布', 'أخذ'],
    answer: {
      en: 'Create once, publish everywhere. The One-to-Many Publishing Engine turns a single idea into an article, short video, long video, audio episode, carousel, newsletter and translations. The AI copilot drafts, schedules and adapts tone per format — you approve every output before it ships.',
      sw: 'Unda mara moja, chapisha kote. Injini ya One-to-Many hugeuza wazo moja kuwa makala, video fupi, video ndefu, sauti, carousel, jarida na tafsiri. Msaidizi wa AI huandaa rasimu na kuratibu — wewe huidhinisha kila toleo kabla halijachapishwa.',
      fr: 'Créez une fois, publiez partout. Le moteur One-to-Many transforme une idée en article, vidéo courte, vidéo longue, épisode audio, carrousel, newsletter et traductions. Le copilote IA rédige et planifie — vous validez chaque sortie avant publication.',
      ar: 'أنشئ مرة واحدة وانشر في كل مكان. يحوّل محرك النشر "واحد إلى متعدد" فكرة واحدة إلى مقال وفيديو قصير وفيديو طويل وحلقة صوتية وعرض شرائح ونشرة بريدية وترجمات. المساعد الذكي يعدّ المسودات وأنت تعتمد كل ناتج قبل نشره.',
      zh: '一次创作，处处发布。一对多发布引擎把一个创意变成文章、短视频、长视频、音频节目、图集、新闻通讯和多语言翻译。AI 副驾驶负责起草和排期——每次发布前都由你审批。',
    },
    steps: [
      'Open Create and describe your idea to the AI copilot.',
      'Pick target formats — article, short video, audio, newsletter…',
      'Review each generated format, approve, and schedule in one flow.',
    ],
    image: '/creator-formats.jpg',
    imageAlt: 'One-to-Many formats: article, video, audio, newsletter as glass cards',
    deepLink: { to: '/creators', label: 'Open Creator Studio' },
  },
  {
    id: 'translation',
    title: { en: 'Translation & Dubbing', sw: 'Tafsiri na Dubbing', fr: 'Traduction & Doublage', ar: 'الترجمة والدبلجة', zh: '翻译与配音' },
    module: 'Language Gateway guide',
    version: 'v2.13.2',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['translate', 'translation', 'dubbing', 'dub', 'lip sync', 'subtitle', 'language', 'tafsiri', 'traduction', 'doublage', 'ترجمة', 'دبلجة', '翻译', '配音', '语言'],
    answer: {
      en: 'Kinjy translates without barriers: auto language detection, one-click "Translate · AI" chips under posts, and a side-by-side view to compare with the original. For video, the pipeline runs speech recognition → transcript → translation → subtitles → AI dubbing → voice-preserving dubbing → lip sync. A provider-independent gateway routes each job to the best model by language, quality, price, latency and privacy — dubbed media always carries the AI Generated label.',
      sw: 'Kinjy hutafsiri bila vizuizi: utambuzi wa lugha kiotomatiki, kitambaa cha "Translate · AI" chini ya machapisho, na mtazamo wa kulinganisha upande kwa upande. Kwa video: utambuzi wa hotuba → maandishi → tafsiri → manukuu → dubbing ya AI → dubbing inayohifadhi sauti → lip sync. Njia hupitia lango huru la lugha, na media iliyodubishwa hubeba lebo ya AI Generated.',
      fr: 'Kinjy traduit sans barrières : détection automatique de la langue, pastille « Traduire · IA » sous chaque publication et vue côte à côte. Pour la vidéo : reconnaissance vocale → transcription → traduction → sous-titres → doublage IA → doublage préservant la voix → synchronisation labiale. Une passerelle indépendante route chaque tâche vers le meilleur modèle ; les médias doublés portent toujours le label AI Generated.',
      ar: 'تترجم كالوتا بلا حواجز: كشف اللغة تلقائيًا، ورقاقة "ترجمة · ذكاء اصطناعي" تحت المنشورات، وعرض جنبًا إلى جنب مع الأصل. وللفيديو: التعرف على الكلام ← نص ← ترجمة ← ترجمة مصاحبة ← دبلجة بالذكاء الاصطناعي ← دبلجة تحافظ على الصوت ← مزامنة الشفاه. توجّه البوابة المستقلة كل مهمة لأفضل نموذج، والوسائط المدبلجة تحمل دائمًا وسم "مولّد بالذكاء الاصطناعي".',
      zh: 'Kinjy 实现无障碍翻译：自动语言检测、帖子下方一键"翻译 · AI"标签、原文对照视图。视频处理管线为：语音识别→转写→翻译→字幕→AI 配音→保留原声配音→唇形同步。独立语言网关按语言、质量、价格、延迟和隐私路由到最佳模型；配音内容始终带有"AI 生成"标签。',
    },
    deepLink: { to: '/platform', label: 'See Translation' },
  },
  {
    id: 'family-tree',
    title: { en: 'Family Tree', sw: 'Mti wa Familia', fr: 'Arbre familial', ar: 'شجرة العائلة', zh: '家族树' },
    module: 'Family Tree guide',
    version: 'v2.14.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['family', 'tree', 'relative', 'verify', 'verification', 'ancestor', 'cousin', 'uncle', 'path', 'familia', 'mti', 'thibitisha', 'famille', 'arbre', 'عائلة', 'شجرة', '家族', '亲属', '验证'],
    answer: {
      en: 'The Family Tree is a verified genealogical graph: Person Nodes and Relationship Edges (parent_of, spouse_of, adoptive_parent_of…), with derived relationships — grandparent, uncle, cousin — computed dynamically. New relatives go PENDING→VERIFIED through member confirmation; deceased members need corroboration from 3 closely related members. Tools include the "How are we related?" path finder, common ancestor finder, closeness ranking and duplicate detection that never auto-merges. The AI never infers paternity, religion or ethnicity — and never fabricates relatives.',
      sw: 'Mti wa Familia ni grafu ya nasaba iliyothibitishwa: Nodi za Watu na Uhusiano (mzazi wa, mwenzi wa, mzazi wa kumpokea…), na uhusiano unao tokana — babu, mjomba, binamu — huhesabiwa kiotomatiki. Jamaa mpya hupita PENDING→VERIFIED kwa uthibitisho wa wanachama; marehemu huhitaji uthibitisho wa wanachama 3 wa karibu. Zana ni "Tuna uhusiano gani?" (path finder), kutafuta mzee wa pamoja, na AI haiwahi kubuni jamaa wala kudokeza baba, dini au kabila.',
      fr: 'L’arbre familial est un graphe généalogique vérifié : nœuds Personne et liens de relation (parent_of, spouse_of, adoptive_parent_of…), les relations dérivées — grand-parent, oncle, cousin — étant calculées dynamiquement. Tout nouveau proche passe de PENDING à VERIFIED par confirmation des membres ; pour un défunt, 3 membres proches doivent corroborer. Outils : « Quel est notre lien ? », recherche d’ancêtre commun, classement de proximité et détection de doublons sans fusion automatique. L’IA n’infère jamais paternité, religion ou ethnicité — et ne fabrique jamais de proches.',
      ar: 'شجرة العائلة رسم بياني لأنساب موثّقة: عقد أشخاص وحواف علاقات (والد_لـ، زوج_لـ، والد_بالتبني…)، وتُحسب العلاقات المشتقة — الجد، العم، ابن العم — ديناميكيًا. الأقارب الجدد ينتقلون من "قيد الانتظار" إلى "موثّق" بتأكيد الأعضاء؛ وللمتوفَّين يلزم تأييد 3 أعضاء مقربين. الأدوات تشمل "ما صلة قرابتنا؟" والبحث عن السلف المشترك وكشف التكرار دون دمج تلقائي. لا يستنتج الذكاء الاصطناعي النسب أو الدين أو العرق أبدًا ولا يختلق أقارب.',
      zh: '家族树是经过验证的谱系图谱：人物节点与关系边（parent_of、spouse_of、adoptive_parent_of……），祖孙、叔伯、堂表亲等派生关系动态计算。新亲属需经成员确认从"待验证"转为"已验证"；已故成员需 3 位近亲佐证。工具包括"我们是什么关系？"路径查找、共同祖先查找、亲密度排序和绝不自动合并的查重。AI 绝不推断亲子关系、宗教或族裔，也绝不虚构亲属。',
    },
    steps: [
      'Open Family Tree and tap "Add relative" on any person node.',
      'Choose the relationship type — derived relations compute themselves.',
      'Ask the relative to confirm; the edge turns VERIFIED with a gold seal.',
    ],
    image: '/assistant-illustration-2.jpg',
    imageAlt: 'Family tree verification flow with three corroborating members',
    deepLink: { to: '/family', label: 'Open Family Tree' },
  },
  {
    id: 'heritage-ai',
    title: { en: 'Family Heritage AI', sw: 'AI ya Urithi wa Familia', fr: 'IA du patrimoine familial', ar: 'ذكاء التراث العائلي', zh: '家族传承 AI' },
    module: 'Family Heritage guide',
    version: 'v2.13.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['heritage', 'archive', 'old photos', 'letters', 'recordings', 'restore', 'documentary', 'biography', 'urithi', 'picha za zamani', 'patrimoine', 'archives', 'تراث', 'أرشيف', '传承', '老照片', '档案'],
    answer: {
      en: 'Upload old photos, letters and recordings: Family Heritage AI restores them, builds an interactive family timeline, drafts biographies and even assembles a family documentary. Original files are always preserved untouched alongside AI-enhanced copies — every enhancement is labeled and reversible.',
      sw: 'Pakia picha za zamani, barua na rekodi: AI ya Urithi wa Familia huzirekebisha, kujenga ratiba maingiliano ya familia, kuandika wasifu na hata kutengeneza filamu ya familia. Faili asili huhifadhiwa bila kuguswa kando ya nakala zilizoboreshwa na AI.',
      fr: 'Téléversez photos anciennes, lettres et enregistrements : l’IA du patrimoine les restaure, construit une frise familiale interactive, rédige des biographies et assemble même un documentaire familial. Les fichiers originaux sont toujours conservés intacts à côté des copies améliorées.',
      ar: 'ارفع الصور القديمة والرسائل والتسجيلات: يقوم ذكاء التراث العائلي بترميمها وبناء خط زمني تفاعلي للعائلة وصياغة سير ذاتية وحتى تجميع فيلم وثائقي عائلي. تُحفظ الملفات الأصلية دائمًا كما هي بجانب النسخ المحسّنة.',
      zh: '上传老照片、信件和录音：家族传承 AI 会修复它们，构建互动家族时间线、撰写传记，甚至制作家族纪录片。原始文件始终原样保留，AI 增强副本均有标注且可逆。',
    },
    image: '/family-archive-2.jpg',
    imageAlt: 'Old letters, a fountain pen and a sepia portrait on cream linen',
    deepLink: { to: '/family', label: 'Open Heritage AI' },
  },
  {
    id: 'graveyard',
    title: { en: 'Digital Graveyard', sw: 'Makaburi ya Kidijitali', fr: 'Cimetière numérique', ar: 'المقبرة الرقمية', zh: '数字纪念园' },
    module: 'Memorials guide',
    version: 'v2.13.1',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['memorial', 'graveyard', 'grave', 'candle', 'flower', 'deceased', 'qr', 'condolence', 'makaburi', 'mnara', 'mémorial', 'cimetière', 'نصب', 'مقبرة', 'شمعة', '纪念', '蜡烛', '讣告'],
    answer: {
      en: 'The Digital Graveyard hosts verified memorials: biography, timeline, photos, videos and voice, guest book and condolences, digital flowers & candles (free and paid), verified grave location with QR memorial codes, and anniversary reminders (10 days, 3 days, 6 hours). Up to 3 administrators manage each memorial with succession rules; death verification flows UNCONFIRMED→REPORTED→UNDER REVIEW→VERIFIED, and faith styles are chosen only from documented wishes — never inferred by AI.',
      sw: 'Makaburi ya Kidijitali huhifadhi kumbukumbu zilizothibitishwa: wasifu, ratiba, picha, video na sauti, kitabu cha wageni, maua na mishumaa ya kidijitali (bure na ya kulipia), eneo la kaburi lililothibitishwa na misimbo ya QR, na vikumbusho vya kumbukumbu. Wasimamizi hadi 3 huisimamia kumbukumbu kwa sheria za urithi; uthibitisho wa kifo hupita UNCONFIRMED→REPORTED→UNDER REVIEW→VERIFIED.',
      fr: 'Le Cimetière numérique héberge des mémoriaux vérifiés : biographie, frise, photos, vidéos et voix, livre d’or et condoléances, fleurs et bougies numériques (gratuites et payantes), localisation vérifiée de la tombe avec codes QR et rappels d’anniversaire (10 j, 3 j, 6 h). Jusqu’à 3 administrateurs avec règles de succession ; la vérification du décès suit UNCONFIRMED→REPORTED→UNDER REVIEW→VERIFIED ; les styles confessionnels ne viennent que de volontés documentées — jamais déduits par l’IA.',
      ar: 'تستضيف المقبرة الرقمية نُصُبًا تذكارية موثّقة: سيرة ذاتية وخط زمني وصور وفيديوهات وصوت وسجل زوار وتعازٍ وزهور وشموع رقمية (مجانية ومدفوعة) وموقع قبر موثّق مع رموز QR وتذكيرات بالذكرى. حتى 3 مديرين لكل نصب مع قواعد تعاقب؛ والتحقق من الوفاة يمر بمراحل "غير مؤكد ← مُبلَّغ ← قيد المراجعة ← موثّق"، وأنماط الطقوس تُختار فقط من وصايا موثقة — لا يستنتجها الذكاء الاصطناعي أبدًا.',
      zh: '数字纪念园托管经过验证的纪念页：传记、时间线、照片、视频和语音、留言簿与悼念、数字鲜花和蜡烛（免费与付费）、经核实的墓地位置与 QR 纪念码、周年提醒（10 天、3 天、6 小时）。每个纪念页最多 3 位管理员并有继承规则；死亡验证流程为未确认→已报告→审核中→已验证；宗教风格只依据有据可查的遗愿选择——AI 绝不推断。',
    },
    steps: [
      'Open Graveyard → "Create memorial" and add biography and photos.',
      'Invite up to 3 administrators and set succession.',
      'Place the verified grave location to generate the QR memorial code.',
    ],
    image: '/memorial-hero.jpg',
    imageAlt: 'A candle and white flowers on dark stone at blue hour',
    deepLink: { to: '/memorials', label: 'Open Graveyard' },
  },
  {
    id: 'marketplace-margin',
    title: { en: 'Marketplace & Margin Model', sw: 'Soko na Mfumo wa Margini', fr: 'Marketplace & Marge', ar: 'السوق ونموذج الهامش', zh: '市场与利润模式' },
    module: 'Marketplace guide',
    version: 'v2.12.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['marketplace', 'sell', 'vendor', 'margin', '20%', 'price', 'buy', 'shop', 'duka', 'soko', 'marché', 'marge', 'سوق', 'هامش', '市场', '利润', '商家'],
    answer: {
      en: 'Vendors set their own price and Kinjy adds a flat 20% margin on top: a $100 vendor price becomes a $120 customer price — the vendor receives their $100 in full. The sponsor’s 20% commission is calculated on the $20 markup, never on the seller’s money, which keeps payouts transparent and reconcilable to the ledger.',
      sw: 'Wachuuzi huweka bei yao wenyewe na Kinjy huongeza margini ya 20% juu yake: bei ya $100 ya mchuuzi huwa $120 kwa mteja — mchuuzi hupokea $100 zake kikamilifu. Kamisheni ya 20% ya mdhamini huhesabiwa kwenye margini ya $20, si pesa ya mchuuzi.',
      fr: 'Les vendeurs fixent leur prix et Kinjy ajoute une marge fixe de 20 % : un prix vendeur de 100 $ devient 120 $ pour le client — le vendeur reçoit ses 100 $ intégralement. Tous les pourcentages d’affiliation sont calculés sur la marge de 20 $, jamais sur l’argent du vendeur.',
      ar: 'يحدد البائعون سعرهم وتضيف كالوتا هامشًا ثابتًا 20% فوقه: سعر 100 دولار يصبح 120 للعميل — ويستلم البائع مائته كاملة. جميع نسب العمولة تُحسب على هامش الـ 20 دولارًا، وليس على أموال البائع.',
      zh: '商家自主定价，Kinjy 统一加收 20% 利润：商家价 100 美元，顾客价 120 美元——商家全额收到 100 美元。所有推广佣金百分比都按 20 美元利润计算，绝不动用商家的货款，账目可对账。',
    },
    steps: [
      'List an item at your vendor price — say $100.',
      'Kinjy shows the customer $120 (vendor price + 20% margin).',
      'When the purchase becomes final you receive $100; the buyer’s sponsor earns 20% of the $20 markup.',
    ],
    image: '/marketplace-hero.jpg',
    imageAlt: 'A digital East African market stall with floating glass price tags',
    deepLink: { to: '/commerce', label: 'Open Marketplace' },
  },
  {
    id: 'advertising',
    title: { en: 'Advertising & AI Ad Engine', sw: 'Matangazo na Injini ya AI', fr: 'Publicité & Moteur IA', ar: 'الإعلانات ومحرك الذكاء', zh: '广告与 AI 广告引擎' },
    module: 'Ads guide',
    version: 'v2.13.1',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['advertising', 'ads', 'ad', 'campaign', 'cpm', 'cpc', 'floor price', 'sponsor', 'tangazo', 'matangazo', 'publicité', 'annonce', 'إعلان', 'إعلانات', '广告', '竞价'],
    answer: {
      en: 'Kinjy advertising runs on competitive, AI-adjusted floor pricing: Standard CPM $0.50, Premium video CPM $1.00, CPC $0.05, CPV $0.005, Engagement $0.02, Leads from $0.25, local promoted posts from $1/day, city sponsorship from $5/day, country & global by auction. Tell the AI Advertising Engine something like "a $100 campaign promoting my restaurant to people aged 25–45 within 20 km of Dar es Salaam" and it recommends the objective, generates creatives, selects audiences, allocates budget and A/B tests — a human always approves before launch.',
      sw: 'Matangazo ya Kinjy hutumia bei za sakafu zinazorekebishwa na AI: CPM ya kawaida $0.50, video CPM $1.00, CPC $0.05, CPV $0.005, ushiriki $0.02, miongozo kuanzia $0.25, machapisho ya eneo kuanzia $1/siku. Ambia Injini ya AI k.m. "kampeni ya $100 kutangaza mgahawa wangu kwa watu 25–45 ndani ya km 20 ya Dar es Salaam" na itapendekeza lengo, kutengeneza kreative na kugawa bajeti — binadamu huidhinisha kabla ya uzinduzi.',
      fr: 'La publicité Kinjy repose sur des prix planchers compétitifs ajustés par IA : CPM standard 0,50 $, CPM vidéo premium 1,00 $, CPC 0,05 $, CPV 0,005 $, engagement 0,02 $, leads dès 0,25 $, posts locaux dès 1 $/jour, sponsoring de ville dès 5 $/jour, pays et monde aux enchères. Dites au moteur IA « une campagne de 100 $ pour mon restaurant auprès des 25–45 ans dans un rayon de 20 km autour de Dar es Salaam » : il propose l’objectif, génère les créas, cible l’audience et teste en A/B — un humain valide toujours avant le lancement.',
      ar: 'تعمل إعلانات كالوتا بأسعار أرضية تنافسية يضبطها الذكاء الاصطناعي: CPM قياسي 0.50 دولار، CPM فيديو مميز 1.00، CPC 0.05، CPV 0.005، تفاعل 0.02، عملاء محتملون من 0.25، منشور محلي من دولار يوميًا، ورعاية مدينة من 5 دولارات يوميًا، والدولة والعالم بالمزاد. أخبر محرك الإعلانات الذكي مثلًا "حملة بـ100 دولار للترويج لمطعمي لمن أعمارهم 25–45 ضمن 20 كم من دار السلام" فيقترح الهدف ويولّد التصاميم ويخصص الميزانية ويختبر A/B — والموافقة البشرية إلزامية قبل الإطلاق.',
      zh: 'Kinjy 广告采用 AI 调整的竞争性底价：标准 CPM $0.50、优质视频 CPM $1.00、CPC $0.05、CPV $0.005、互动 $0.02、线索 $0.25 起、本地推广 $1/天起、城市赞助 $5/天起、国家级和全球以竞拍定价。只需告诉 AI 广告引擎"用 100 美元向达累斯萨拉姆 20 公里内 25–45 岁人群推广我的餐厅"，它会推荐目标、生成创意、选择受众、分配预算并做 A/B 测试——投放前始终需要人工批准。',
    },
    image: '/ads-engine.jpg',
    imageAlt: 'AI ad campaign radiating a 20 km circle over a night city',
    deepLink: { to: '/commerce', label: 'Open Ads' },
  },
  {
    id: 'subscriptions',
    title: { en: 'Subscriptions & Pricing', sw: 'Usajili na Bei', fr: 'Abonnements & Tarifs', ar: 'الاشتراكات والأسعار', zh: '订阅与价格' },
    module: 'Pricing guide',
    version: 'v2.12.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['subscription', 'pricing', 'price', 'premium', 'basic', 'free', 'plan', 'usajili', 'bei', 'abonnement', 'tarif', 'اشتراك', 'سعر', '订阅', '价格', '会员'],
    answer: {
      en: 'Three tiers: Free ($0 — genuinely useful forever), Basic $4.99/mo or $49/yr (HD video, advanced translation, scheduled posts, more AI credits, newsletters), and Premium $9.99/mo or $99/yr (4K, AI Creator Studio, dubbing, lip-sync, AI clips, brand kit, custom algorithm feeds, API allowance, premium themes). One-off purchases are priced 2–4× the implied subscription unit cost, and KYC verification is required before payouts.',
      sw: 'Ngazi tatu: Free ($0 — muhimu milele), Basic $4.99/mwezi au $49/mwaka (video HD, tafsiri za hali ya juu, machapisho yaliyoratibiwa, mikopo zaidi ya AI), na Premium $9.99/mwezi au $99/mwaka (4K, AI Creator Studio, dubbing, lip-sync, AI clips, brand kit, algorithm maalum, API). Ununuzi wa mara moja huuzwa mara 2–4 ya gharama ya usajili.',
      fr: 'Trois niveaux : Free (0 $ — réellement utile), Basic 3,99 $/mois ou 39 $/an (vidéo HD, traduction avancée, posts planifiés, plus de crédits IA, newsletters) et Premium 9,99 $/mois ou 99 $/an (4K, Studio Créateur IA, doublage, lip-sync, clips IA, kit de marque, fils algorithmiques personnalisés, quota API, thèmes premium). Les achats uniques coûtent 2 à 4× le coût unitaire d’abonnement.',
      ar: 'ثلاث باقات: مجانية (0 دولار — مفيدة فعلًا)، الأساسية 4.99 دولار شهريًا أو 49 سنويًا (فيديو HD وترجمة متقدمة وجدولة المنشورات ورصيد ذكاء اصطناعي أكبر)، والمميزة 9.99 شهريًا أو 99 سنويًا (4K واستوديو المبدعين الذكي والدبلجة ومزامنة الشفاه ومقاطع الذكاء وعدة العلامة وخلاصات خوارزمية مخصصة وحصة API). المشتريات المنفردة تُسعّر بـ2–4 أضعاف كلفة وحدة الاشتراك.',
      zh: '三个档位：免费版（$0——真正实用）、基础版 $4.99/月或 $49/年（高清视频、高级翻译、定时发布、更多 AI 额度、新闻通讯）、高级版 $9.99/月或 $99/年（4K、AI 创作工作室、配音、唇形同步、AI 剪辑、品牌工具包、自定义算法信息流、API 额度、高级主题）。单次购买定价为订阅单位成本的 2–4 倍，提现前需完成 KYC 验证。',
    },
    deepLink: { to: '/pricing', label: 'See Pricing' },
  },
  {
    id: 'kyc',
    title: { en: 'KYC Verification', sw: 'Uthibitisho wa KYC', fr: 'Vérification KYC', ar: 'التحقق من الهوية (KYC)', zh: 'KYC 验证' },
    module: 'KYC guide',
    version: 'v2.11.3',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['kyc', 'verify identity', 'identity', 'verification fee', 'payout requirement', 'uthibitisho', 'vérification', 'identité', 'تحقق', 'هوية', '实名', '验证'],
    answer: {
      en: 'KYC runs through the KinjyKYC API for $10 per year and is required before earning commission or withdrawing earnings. Only the verification result is stored on-platform — your identity documents never live on Kinjy servers, and re-verification reminders arrive before expiry.',
      sw: 'KYC hufanyika kupitia KinjyKYC API kwa $10 kwa mwaka na inahitajika kabla ya kupata kamisheni au kutoa mapato. Matokeo ya uthibitisho pekee ndio huhifadhiwa — nyaraka zako za utambulisho hazihifadhiwi kwenye seva za Kinjy.',
      fr: 'Le KYC passe par l’API KinjyKYC pour 10 $/an, requis avant de rejoindre le programme d’affiliation ou de retirer des gains. Seul le résultat de vérification est conservé sur la plateforme — vos documents d’identité ne résident jamais sur les serveurs Kinjy.',
      ar: 'يُجرى التحقق عبر واجهة KinjyKYC مقابل 10 دولارات سنويًا وهو مطلوب قبل الانضمام لبرنامج العمولة أو سحب الأرباح. تُحفظ نتيجة التحقق فقط على المنصة — وثائق هويتك لا تُخزن أبدًا على خوادم كالوتا.',
      zh: 'KYC 通过 KinjyKYC API 完成，每年 10 美元，参与推广计划或提现前必须完成。平台只保存验证结果——您的身份证件绝不存储在 Kinjy 服务器上，到期前会收到续验提醒。',
    },
    steps: [
      'Settings → Verification → start KinjyKYC ($10/year).',
      'Complete the check in the secure KinjyKYC flow.',
      'Your badge turns Verified; only the result is stored on-platform.',
    ],
    deepLink: { to: '/pricing', label: 'KYC note' },
  },
  {
    id: 'earnings',
    title: { en: 'Earnings, commission & Kinjy Leaders', sw: 'Mapato, kamisheni na Kinjy Leaders', fr: 'Revenus, commission & Kinjy Leaders', ar: 'الأرباح والعمولة وبرنامج Kinjy Leaders', zh: '收益、佣金与 Kinjy Leaders' },
    module: 'Earnings guide',
    version: 'v2.14.0',
    roles: ['member', 'admin'],
    keywords: ['earnings', 'payout', 'affiliate', 'referral', 'leader', 'pool', 'revenue share', 'commission', 'withdraw', 'mapato', 'revenus', 'gains', 'affiliation', 'أرباح', 'عمولة', 'أرباحا', '收益', '佣金', '提现', 'pesa'],
    answer: {
      en: 'Creators keep 40% of ad revenue. The member who sponsored them is then paid 20% of what Kinjy retains — one level, and only that one: nobody above your sponsor earns on what you do. Where Kinjy connects a buyer to a seller, the commission comes out of the 20% markup Kinjy adds on top of the seller’s price, never out of the seller’s money. Kinjy Leaders shares 5% of monthly company revenue between the 10,000 members with the highest direct commissions that month, in proportion to those commissions, after a fraud review. Withdrawals need KYC and reconcile to the immutable ledger.',
      sw: 'Waundaji hubaki na 40% ya mapato ya matangazo. Kisha aliyemdhamini hulipwa 20% ya kiasi anachobaki nacho Kinjy — ngazi moja tu: hakuna yeyote juu ya mdhamini wako anayepata chochote. Pale Kinjy inapounganisha mnunuzi na muuzaji, kamisheni hutoka kwenye margini ya 20% ambayo Kinjy huongeza juu ya bei ya muuzaji, si kwenye pesa za muuzaji. Kinjy Leaders hugawa 5% ya mapato ya kampuni ya mwezi kwa wanachama 10,000 wenye kamisheni kubwa zaidi mwezi huo, kwa uwiano wa kamisheni hizo. Kutoa pesa kunahitaji KYC.',
      fr: 'Les créateurs gardent 40 % des revenus publicitaires. Le membre qui les a parrainés touche ensuite 20 % de ce que Kinjy conserve — un seul niveau, et uniquement celui-là : personne au-dessus de votre parrain ne gagne sur ce que vous faites. Lorsque Kinjy met en relation un acheteur et un vendeur, la commission sort de la marge de 20 % que Kinjy ajoute au prix du vendeur, jamais de l’argent du vendeur. Kinjy Leaders partage 5 % des revenus mensuels de l’entreprise entre les 10 000 membres ayant gagné le plus de commissions directes dans le mois, au prorata de ces commissions, après contrôle anti-fraude. Les retraits exigent le KYC.',
      ar: 'يحتفظ المبدعون بنسبة 40% من عائد الإعلانات. ثم يُدفع للعضو الذي رعاهم 20% مما تحتفظ به Kinjy — مستوى واحد فقط: لا أحد فوق راعيك يكسب مما تفعله. وحين تربط Kinjy مشتريًا ببائع، تُدفع العمولة من هامش الـ20% الذي تضيفه Kinjy فوق سعر البائع، لا من مال البائع. ويوزّع برنامج Kinjy Leaders نسبة 5% من إيرادات الشركة الشهرية على الـ10,000 عضو الأعلى عمولة مباشرة في ذلك الشهر، بالتناسب مع تلك العمولات، بعد مراجعة الاحتيال. السحوبات تتطلب التحقق من الهوية.',
      zh: '创作者保留 40% 的广告收入。推荐他们的成员随后可获得 Kinjy 留存部分的 20%——只有这一层：你的推荐人之上的任何人都不会从你的活动中获利。当 Kinjy 为买卖双方牵线时，佣金来自 Kinjy 在卖家价格之上加收的 20% 加价，绝不来自卖家的钱。Kinjy Leaders 将公司每月收入的 5% 分配给当月直接佣金最高的 10,000 名成员，按其佣金比例分配，并经防欺诈审核。提现需完成 KYC 并与不可篡改账本对账。',
    },
    steps: [
      'Open Earnings to see your revenue split and pending balance.',
      'Complete KYC ($10/year) to unlock withdrawals.',
      'Request a payout — it joins the next reconciled payment batch.',
    ],
    deepLink: { to: '/creators', label: 'See Earnings' },
  },
  {
    id: 'privacy',
    title: { en: 'Privacy Center', sw: 'Kituo cha Faragha', fr: 'Centre de confidentialité', ar: 'مركز الخصوصية', zh: '隐私中心' },
    module: 'Privacy guide',
    version: 'v2.13.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['privacy', 'data', 'personal data', 'tracking', 'consent', 'faragha', 'confidentialité', 'données', 'خصوصية', 'بيانات', '隐私', '数据'],
    answer: {
      en: 'The Privacy Center shows exactly what Kinjy holds about you and why, with per-purpose consent toggles. Translation runs through the provider-independent gateway chosen for data sensitivity; passkeys keep biometrics on your device; and self-service deletion erases your data on the schedule you choose — GDPR/PDPA compliant, no justification required.',
      sw: 'Kituo cha Faragha huonyesha haswa data gani Kinjy inashikilia kukuhusu na kwa nini, kwa vipengele vya ridhaa kwa kila kusudio. Tafsiri hupitia lango huru lililochaguliwa kwa unyeti wa data; passkeys huhifadi bayometriki kwenye kifaa chako; na kufuta akaunti kwa kujitegemea hufuta data zako kwa ratiba unayochagua.',
      fr: 'Le Centre de confidentialité montre exactement ce que Kinjy détient sur vous et pourquoi, avec des bascules de consentement par finalité. La traduction passe par la passerelle choisie selon la sensibilité des données ; les passkeys gardent la biométrie sur votre appareil ; la suppression en libre-service efface vos données selon le calendrier choisi — conforme RGPD/PDPA.',
      ar: 'يعرض مركز الخصوصية بدقة ما تحتفظ به كالوتا عنك ولماذا، مع مفاتيح موافقة لكل غرض. تجري الترجمة عبر البوابة المختارة وفق حساسية البيانات؛ وتحفظ مفاتيح المرور القياسات الحيوية على جهازك؛ والحذف الذاتي يمحو بياناتك بالجدول الذي تختاره — امتثالًا لـ GDPR/PDPA.',
      zh: '隐私中心精确展示 Kinjy 持有您的哪些数据及用途，并提供按目的的同意开关。翻译经由按数据敏感度选择的独立网关处理；通行密钥把生物特征留在您的设备上；自助删除按您选择的时间表清除数据——符合 GDPR/PDPA，无需说明理由。',
    },
    deepLink: { to: '/safety', label: 'Open Privacy Center' },
  },
  {
    id: 'passkeys',
    title: { en: 'Passkeys & Login Security', sw: 'Passkeys na Usalama wa Kuingia', fr: 'Passkeys & Sécurité', ar: 'مفاتيح المرور وأمان الدخول', zh: '通行密钥与登录安全' },
    module: 'Security guide',
    version: 'v2.13.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['passkey', 'passkeys', 'biometric', 'fingerprint', 'face', 'login', '2fa', 'otp', 'hardware key', 'password', 'usaimbalaji', 'mot de passe', 'كلمة المرور', 'بصمة', '密钥', '指纹', '登录'],
    answer: {
      en: 'Kinjy uses device-level passkeys: your phone or computer unlocks locally with its own biometrics — there is no central fingerprint or facial database, ever. You can add email OTP, authenticator apps and hardware keys, review devices and get login alerts for anything unusual.',
      sw: 'Kinjy hutumia passkeys za kiwango cha kifaa: simu au kompyuta yake hufungua kwa bayometriki yake — hakuna hifadhidata ya kati ya vidole au nyuso, kamwe. Unaweza kuongeza OTP ya barua pepe, programu za authenticator na funguo za vifaa, na kupokea arifa za kuingia.',
      fr: 'Kinjy utilise des passkeys au niveau de l’appareil : votre téléphone ou ordinateur déverrouille localement avec sa propre biométrie — il n’existe aucune base centrale d’empreintes ou de visages. Ajoutez OTP par e-mail, applications d’authentification et clés matérielles, gérez vos appareils et recevez des alertes de connexion.',
      ar: 'تستخدم كالوتا مفاتيح مرور على مستوى الجهاز: هاتفك أو حاسوبك يفتح محليًا بقياساته الحيوية — لا توجد أي قاعدة بيانات مركزية للبصمات أو الوجوه إطلاقًا. ويمكنك إضافة رموز البريد الإلكتروني وتطبيقات المصادقة والمفاتيح المادية، ومراجعة الأجهزة وتلقي تنبيهات الدخول.',
      zh: 'Kinjy 使用设备级通行密钥：手机或电脑用自身生物特征在本地解锁——绝不存在集中的指纹或人脸数据库。您还可以添加邮箱验证码、验证器应用和硬件密钥，管理设备并接收异常登录提醒。',
    },
    steps: [
      'Settings → Security → "Add a passkey" on this device.',
      'Unlock with your device biometrics — nothing leaves the device.',
      'Optionally add an authenticator app or hardware key as backup.',
    ],
    deepLink: { to: '/safety', label: 'See Security' },
  },
  {
    id: 'account-deletion',
    title: { en: 'Account Deletion', sw: 'Kufuta Akaunti', fr: 'Suppression de compte', ar: 'حذف الحساب', zh: '注销账号' },
    module: 'Account guide',
    version: 'v2.12.2',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['delete', 'deactivate', 'account deletion', 'close account', 'leave', 'gdpr', 'futa akaunti', 'supprimer', 'compte', 'حذف الحساب', '注销', '删除账号'],
    answer: {
      en: 'Self-service, no justification required: Settings → Account → Deactivate or Delete. After identity confirmation you can choose an optional cooling period before erasure becomes permanent; the process is GDPR/PDPA compliant, and memorial succession rules protect any family trees or memorials you administer.',
      sw: 'Kwa kujitegemea, bila kutoa sababu: Settings → Account → Deactivate au Delete. Baada ya kuthibitisha utambulisho unaweza kuchagua kipindi cha kusubiri kabla ya kufutwa kabisa; mchakato unazingatia GDPR/PDPA, na sheria za urithi wa kumbukumbu hulinda miti ya familia unayosimamia.',
      fr: 'En libre-service, sans justification : Paramètres → Compte → Désactiver ou Supprimer. Après confirmation d’identité, vous pouvez choisir une période de réflexion avant l’effacement définitif ; le processus est conforme RGPD/PDPA et les règles de succession protègent arbres familiaux et mémoriaux que vous administrez.',
      ar: 'بخدمة ذاتية ودون تبرير: الإعدادات ← الحساب ← تعطيل أو حذف. بعد تأكيد الهوية يمكنك اختيار فترة تهدئة اختيارية قبل أن يصبح المحو نهائيًا؛ والعملية متوافقة مع GDPR/PDPA، وقواعد التعاقب على النُّصُب تحمي أشجار العائلة والنُّصُب التي تديرها.',
      zh: '完全自助、无需说明理由：设置→账号→停用或删除。身份确认后，您可以选择可选的冷静期再永久清除；流程符合 GDPR/PDPA，纪念继承规则会保护您管理的家族树和纪念页。',
    },
    deepLink: { to: '/safety', label: 'Account control' },
  },
  {
    id: 'safety',
    title: { en: 'Safety Layers & Moderation', sw: 'Tabaka za Usalama', fr: 'Couches de sécurité', ar: 'طبقات الأمان', zh: '安全层级与审核' },
    module: 'Safety guide',
    version: 'v2.13.1',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['safety', 'moderation', 'report', 'community notes', 'age mode', 'teen', 'provenance', 'usalama', 'sécurité', 'modération', 'أمان', 'إشراف', '安全', '审核', '举报'],
    answer: {
      en: 'Safety is layered: automated filters → community moderation → platform review → appeals → public transparency reports. Child-safe public browsing, teen mode and adult mode set boundaries; Community Notes add context without ever auto-declaring truth; and every media item carries a provenance label — Original Upload, Edited, AI Assisted, AI Generated or Verified Source.',
      sw: 'Usalama una tabaka: vichujio vya kiotomatiki → usimamizi wa jamii → mapitio ya jukwaa → rufaa → ripoti za uwazi za umma. Kuvinjari kwa watoto, teen mode na adult mode huweka mipaka; Community Notes huongeza muktadha bila kutangaza ukweli; na kila media hubeba lebo ya asili — Original Upload, Edited, AI Assisted, AI Generated au Verified Source.',
      fr: 'La sécurité est en couches : filtres automatisés → modération communautaire → revue plateforme → appels → rapports publics de transparence. Navigation enfants, mode ado et mode adulte fixent les limites ; les Notes communautaires ajoutent du contexte sans jamais décréter la vérité ; chaque média porte un label de provenance.',
      ar: 'الأمان متعدد الطبقات: مرشحات آلية ← إشراف المجتمع ← مراجعة المنصة ← استئناف ← تقارير شفافية علنية. تصفح آمن للأطفال ووضع للمراهقين ووضع للبالغين؛ وملاحظات المجتمع تضيف سياقًا دون أن تعلن الحقيقة؛ وكل وسيط يحمل وسم مصدر: رفع أصلي، معدّل، بمساعدة ذكاء اصطناعي، مولّد بالذكاء الاصطناعي أو مصدر موثّق.',
      zh: '安全是分层的：自动过滤→社区审核→平台复核→申诉→公开透明度报告。儿童安全浏览、青少年模式和成人模式设定边界；社区注释只补充背景，绝不自动断言真相；每条媒体都带有来源标签——原始上传、已编辑、AI 辅助、AI 生成或已验证来源。',
    },
    image: '/safety-hero.jpg',
    imageAlt: 'A shield formed from softly glowing glass layers',
    deepLink: { to: '/safety', label: 'Open Safety' },
  },
  {
    id: 'developers',
    title: { en: 'Developer Platform', sw: 'Jukwaa la Wasanidi', fr: 'Plateforme développeurs', ar: 'منصة المطورين', zh: '开发者平台' },
    module: 'Developer docs',
    version: 'v2.13.2',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['developer', 'api', 'graphql', 'webhook', 'oauth', 'sdk', 'publish algorithm', 'app marketplace', 'msanidi', 'développeur', 'مطور', '开发者', '接口'],
    answer: {
      en: 'The Developer Platform offers REST and GraphQL APIs, webhooks and OAuth, plus the App Marketplace. Developers can publish feed algorithms to the Algorithm Marketplace via API, and the platform is agent-readable: semantic APIs with licensing and paid AI access let external agents consume Kinjy knowledge legitimately.',
      sw: 'Jukwaa la Wasanidi lina API za REST na GraphQL, webhooks na OAuth, pamoja na App Marketplace. Wasanidi wanaweza kuchapisha algorithm za mlisho kupitia API, na jukwaa linasomeka na mawakala (agent-readable): API za kisemantiki kwa leseni na ufikiaji wa AI wa kulipia.',
      fr: 'La plateforme développeurs propose des API REST et GraphQL, des webhooks et OAuth, plus l’App Marketplace. Les développeurs peuvent publier des algorithmes de fil via l’API, et la plateforme est lisible par les agents : API sémantiques sous licence avec accès IA payant.',
      ar: 'توفر منصة المطورين واجهات REST وGraphQL وwebhooks وOAuth إضافة إلى متجر التطبيقات. ويمكن للمطورين نشر خوارزميات الخلاصات عبر الواجهة، والمنصة قابلة للقراءة من الوكلاء: واجهات دلالية مرخّصة مع وصول مدفوع للذكاء الاصطناعي.',
      zh: '开发者平台提供 REST 和 GraphQL API、webhooks 与 OAuth，以及应用市场。开发者可通过 API 向算法市场发布信息流算法；平台还支持智能体可读（agent-readable）：带授权和付费 AI 访问的语义化 API。',
    },
    image: '/dev-hero.jpg',
    imageAlt: 'Code editor fragment with golden arc motifs',
    deepLink: { to: '/developers', label: 'Open Developer Docs' },
  },
  /* ------------------------- Admin-only entries ------------------------- */
  {
    id: 'admin-ledger',
    title: { en: 'Ledger Reconciliation', sw: 'Upatanishaji wa Ledger', fr: 'Rapprochement du grand livre', ar: 'تسوية دفتر الأستاذ', zh: '账本对账' },
    module: 'Admin Console — Finance',
    version: 'v2.14.0',
    roles: ['admin'],
    adminOnly: true,
    keywords: ['ledger', 'reconcile', 'reconciliation', 'transaction ledger', 'general ledger', 'finance', 'accounting', 'ledgeri', 'grand livre', 'دفتر الأستاذ', '账本', '对账'],
    answer: {
      en: 'The admin console exposes the immutable Transaction Ledger and General Ledger (Singapore accounting standard). Wallet balances must reconcile to ledger entries — the Finance view shows reconciliation status per account, drift alerts, and the payment-batch tool for commission and Kinjy Leaders payouts. Every batch is fraud-reviewed before release.',
      sw: 'Konsoli ya msimamizi huonyesha Transaction Ledger na General Ledger zisizobadilika. Salio za pochi lazima zilingane na entries za ledger — muonekano wa Fedha huonyesha hali ya upatanishaji, arifa za tofauti, na zana ya payment-batch. Kila batch hupitiwa ukaguzi wa udanganyifu kabla ya kutolewa.',
      fr: 'La console admin expose le Transaction Ledger et le General Ledger immuables (norme comptable de Singapour). Les soldes des portefeuilles doivent se rapprocher des écritures — la vue Finance montre le statut de rapprochement par compte, les alertes d’écart et l’outil de lots de paiement. Chaque lot est contrôlé anti-fraude avant déblocage.',
      ar: 'تعرض وحدة تحكم المشرف دفتر المعاملات ودفتر الأستاذ العام غير القابلين للتغيير (المعيار المحاسبي السنغافوري). يجب أن تتطابق أرصدة المحافظ مع قيود الدفتر — تعرض شاشة المالية حالة التسوية لكل حساب وتنبيهات الانحراف وأداة دفعات الدفع. كل دفعة تخضع لمراجعة الاحتيال قبل الإفراج.',
      zh: '管理控制台展示不可篡改的交易账本和总账（新加坡会计准则）。钱包余额必须与账本分录对账——财务视图显示每个账户的对账状态、偏差警报以及推广和领袖池的批量付款工具。每个批次发放前都经过防欺诈审核。',
    },
  },
  {
    id: 'admin-fraud',
    title: { en: 'Anti-Fraud Center', sw: 'Kituo cha Kupambana na Udanganyifu', fr: 'Centre anti-fraude', ar: 'مركز مكافحة الاحتيال', zh: '反欺诈中心' },
    module: 'Admin Console — Fraud',
    version: 'v2.14.0',
    roles: ['admin'],
    adminOnly: true,
    keywords: ['fraud', 'anti-fraud', 'self-referral', 'click fraud', 'fake kyc', 'bot farm', 'risk', 'udanganyifu', 'fraude', 'احتيال', '欺诈'],
    answer: {
      en: 'The Anti-Fraud Center scores referral and ad activity for self-referral rings, click fraud, fake KYC and bot farms. Flagged cases enter a review queue with evidence bundles; confirmed fraud freezes payouts and reverses commissions. The fraud-review step is mandatory before every Kinjy Leaders payment batch.',
      sw: 'Kituo cha Kupambana na Udanganyifu hupiga alama shughuli za marejeo na matangazo kwa self-referral, click fraud, KYC bandia na bot farms. Kesi zilizoalamiwa huingia kwenye foleni ya mapitio zenye ushahidi; udanganyifu uliothibitishwa hufungia malipo na kufuta kamisheni.',
      fr: 'Le Centre anti-fraude note l’activité de parrainage et publicitaire : auto-parrainage, fraude au clic, faux KYC et fermes de bots. Les cas signalés rejoignent une file de revue avec faisceaux de preuves ; la fraude confirmée gèle les paiements et annule les commissions. L’étape anti-fraude est obligatoire avant chaque lot Kinjy Leaders.',
      ar: 'يقيّم مركز مكافحة الاحتيال نشاط الإحالة والإعلانات لكشف حلقات الإحالة الذاتية والنقرات الاحتيالية ووثائق الهوية المزيفة ومزارع الروبوتات. تدخل الحالات المعلَّمة طابور مراجعة مع حزم أدلة؛ والاحتيال المؤكد يجمّد المدفوعات ويعكس العمولات. ومراجعة الاحتيال إلزامية قبل كل دفعة من مجمع القادة.',
      zh: '反欺诈中心对推广和广告活动评分，识别自我推荐团伙、点击欺诈、虚假 KYC 和僵尸农场。标记案件连证据包进入审核队列；确认的欺诈会冻结付款并撤销佣金。每个领袖池付款批次前都必须经过防欺诈审核。',
    },
  },
  {
    id: 'admin-kyc-queue',
    title: { en: 'KYC Review Queue', sw: 'Foleni ya KYC', fr: 'File de revue KYC', ar: 'طابور مراجعة الهوية', zh: 'KYC 审核队列' },
    module: 'Admin Console — KYC',
    version: 'v2.11.3',
    roles: ['admin'],
    adminOnly: true,
    keywords: ['kyc queue', 'verification queue', 'pending verification', 'approve kyc', 'foleni ya kyc', 'file kyc', 'طابور التحقق', '审核队列'],
    answer: {
      en: 'The KYC queue lists pending, expiring and failed verifications from KinjyKYC with result-only records. Admins approve, request re-verification or suspend commission eligibility; documents themselves never transit the platform — decisions reference the provider’s verification token.',
      sw: 'Foleni ya KYC huonyesha uthibitisho unaosubiri, unaokaribia kuisha na ulioshindwa kutoka KinjyKYC kwa rekodi za matokeo pekee. Wasimamizi huidhinisha, kuomba uthibitisho upya au kusitisha ustahiki wa kamisheni; nyaraka zenyewe hazipiti jukwaani.',
      fr: 'La file KYC liste les vérifications en attente, expirantes et échouées de KinjyKYC, avec des enregistrements limités au résultat. Les admins approuvent, demandent une re-vérification ou suspendent l’éligibilité d’affiliation ; les documents ne transitent jamais par la plateforme.',
      ar: 'يعرض طابور التحقق الحالات المعلقة والمنتهية والفاشلة من KinjyKYC بسجلات النتائج فقط. يعتمد المشرفون أو يطلبون إعادة التحقق أو يعلقون أهلية العمولة؛ والوثائق نفسها لا تمر عبر المنصة إطلاقًا.',
      zh: 'KYC 队列列出 KinjyKYC 待审核、即将过期和失败的验证，只保存结果记录。管理员可批准、要求重新验证或暂停推广资格；证件本身绝不经过平台，决定仅引用服务商的验证令牌。',
    },
  },
  {
    id: 'admin-aiwatch',
    title: { en: 'AI Industry Watch', sw: 'Ufuatiliaji wa Sekta ya AI', fr: 'Veille IA', ar: 'مرصد صناعة الذكاء الاصطناعي', zh: 'AI 行业观察' },
    module: 'Admin Console — AI Watch',
    version: 'v2.14.0',
    roles: ['admin'],
    adminOnly: true,
    keywords: ['ai watch', 'industry watch', 'development', 'advisory', 'instruct to execute', 'frontier', 'model news', 'veille', 'مرصد', 'تطوير جديد', '行业观察', '前沿'],
    answer: {
      en: 'AI Watch observes developments in AI and adjacent industry practice, then files an advisory only when adoption would add real value to Kinjy. Each card states what happened, why we should adopt it, why a codebase change is required (with the systems touched) and a proposed branch with sandbox test results. You can review the diff, dismiss, or instruct the assistant to execute — execution always opens a pull request, and human merge approval is always required.',
      sw: 'AI Watch hufuatilia maendeleo ya AI na mbinu za sekta, kisha kuwasilisha ushauri tu pale ambapo kutekeleza kungeongeza thamani kwa Kinjy. Kila kadi hueleza kilichotokea, kwa nini tupitie, kwa nini mabadiliko ya msimbo yanahitajika, na pendekezo la branch na matokeo ya majaribio. Unaweza kupitia diff, kukataa, au kuagiza itekelezwe — utekelezaji hufungua pull request, na idhini ya binadamu ni lazima kila wakati.',
      fr: 'AI Watch observe les développements de l’IA et des pratiques du secteur, puis ne dépose un avis que lorsque l’adoption apporterait une vraie valeur à Kinjy. Chaque carte indique ce qui s’est passé, pourquoi l’adopter, pourquoi un changement de code est requis (systèmes touchés) et propose une branche avec résultats de tests en bac à sable. Vous pouvez relire le diff, ignorer ou ordonner l’exécution — qui ouvre toujours une pull request, avec validation humaine obligatoire.',
      ar: 'يراقب "مرصد الذكاء" تطورات الذكاء الاصطناعي وممارسات الصناعة، ولا يرفع توصية إلا حين يضيف التبني قيمة حقيقية لكالوتا. كل بطاقة توضح ما حدث ولماذا نتبناه ولماذا يلزم تغيير في الكود (والأنظمة المتأثرة) مع فرع مقترح ونتائج اختبارات في بيئة معزولة. يمكنك مراجعة الفرق أو الرفض أو الأمر بالتنفيذ — والتنفيذ يفتح دائمًا طلب دمج، وموافقة بشرية على الدمج إلزامية دائمًا.',
      zh: 'AI 行业观察跟踪 AI 及相关行业的最新动态，只有当采纳能为 Kinjy 带来实际价值时才会提交建议。每张卡片说明发生了什么、为什么值得采纳、为什么需要代码变更（涉及哪些系统），并附带沙盒测试通过的分支提案。您可以审查差异、忽略，或指示助手执行——执行总是以提交 Pull Request 的形式进行，并且始终需要人工合并批准。',
    },
    deepLink: { to: '/admin', label: 'Open Admin Console' },
  },
  {
    id: 'crypto-payments',
    title: { en: 'Paying on Kinjy', sw: 'Kulipa kwenye Kinjy', fr: 'Payer sur Kinjy', ar: 'الدفع على Kinjy', zh: '在 Kinjy 上付款' },
    module: 'Payments guide',
    version: 'v2.17.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['pay', 'payment', 'checkout', 'sonco pay', 'kaluta pay', 'mobile money', 'card', 'crypto', 'usdt', 'malipo', 'paiement', 'دفع', '支付', '付款'],
    answer: {
      en: 'Payments on Kinjy are processed by Sonco Pay, on Kinjy’s own page, with the methods offered in your country — for example mobile money, card or crypto. Kaluta Pay connects to Sonco Pay and is the base of Kinjy’s accounting: it confirms the invoice, records how the payment is split, and holds what is owed until it is due. Kinjy checks each confirmation with the provider before anything moves, and every amount is recorded in Kinjy’s ledger.',
      sw: 'Malipo kwenye Kinjy hushughulikiwa na Sonco Pay, kwenye ukurasa wa Kinjy wenyewe, kwa njia zinazopatikana nchini mwako — kwa mfano pesa za simu, kadi au crypto. Kaluta Pay huunganishwa na Sonco Pay na ndiyo msingi wa uhasibu wa Kinjy: huthibitisha ankara, hurekodi jinsi malipo yanavyogawanywa, na hushikilia kinachodaiwa hadi wakati wake. Kinjy huhakiki kila uthibitisho kwa mtoa huduma kabla chochote hakijasogea, na kila kiasi kinarekodiwa kwenye ledger ya Kinjy.',
      fr: 'Les paiements sur Kinjy sont traités par Sonco Pay, sur la page de Kinjy, avec les moyens proposés dans votre pays — par exemple mobile money, carte ou crypto. Kaluta Pay est relié à Sonco Pay et sert de base à la comptabilité de Kinjy : il confirme la facture, enregistre la répartition du paiement et conserve ce qui est dû jusqu’à son échéance. Kinjy vérifie chaque confirmation auprès du prestataire avant que quoi que ce soit ne bouge, et chaque montant est inscrit au registre de Kinjy.',
      ar: 'تتم معالجة المدفوعات على Kinjy عبر Sonco Pay، على صفحة Kinjy نفسها، بالوسائل المتاحة في بلدك — مثل المال عبر الهاتف المحمول أو البطاقة أو العملات الرقمية. يتصل Kaluta Pay بـ Sonco Pay ويشكّل أساس محاسبة Kinjy: يؤكد الفاتورة، ويسجل كيفية تقسيم الدفعة، ويحتفظ بما هو مستحق حتى موعده. تتحقق Kinjy من كل تأكيد لدى مزوّد الخدمة قبل أي حركة، ويُسجَّل كل مبلغ في السجل المحاسبي لـ Kinjy.',
      zh: 'Kinjy 上的付款由 Sonco Pay 在 Kinjy 自己的页面上处理，可使用您所在国家提供的方式——例如移动支付、银行卡或加密货币。Kaluta Pay 与 Sonco Pay 对接，是 Kinjy 会计集成的基础：它确认账单、记录付款的分配，并在到期前保管应付款项。Kinjy 在任何资金变动之前都会向服务商核实每一次确认，每一笔金额都会记录在 Kinjy 的账本中。',
    },
    steps: [
      'Start the payment from the page of what you are buying (membership, ad credit, a marketplace item).',
      'Choose one of the methods Sonco Pay offers in your country and follow the steps on the page.',
      'Wait for the confirmation: Kinjy reads it back from the provider before anything is unlocked.',
    ],
    deepLink: { to: '/payments', label: 'Open Payments' },
  },
  {
    id: 'cashout-engine',
    title: { en: 'Withdrawing commissions ($10 minimum)', sw: 'Kutoa kamisheni (kiwango cha chini $10)', fr: 'Retrait des commissions (minimum 10 $)', ar: 'سحب العمولات (الحد الأدنى 10 دولارات)', zh: '佣金提现（最低 10 美元）' },
    module: 'Payments guide',
    version: 'v2.16.0',
    roles: ['member', 'admin'],
    keywords: ['cashout', 'withdraw', 'withdrawal', 'commission', 'minimum', '$10', 'ten dollars', 'threshold', 'payout', 'kutoa', 'kamisheni', 'retrait', 'minimum', 'سحب', 'عمولة', '提现', '佣金', '最低'],
    answer: {
      en: 'Your commission is 20% of Kinjy’s revenue on what the members you sponsored do, and every amount is recorded in the ledger. You can withdraw once your available balance reaches $10, network fee included, with your identity verified and the wallet you want to be paid to added and confirmed. Payouts are sent in USDT on BSC; the network fee is shown before you withdraw and taken from the amount sent. Below $10, your balance stays in your wallet until it reaches the minimum.',
      sw: 'Kamisheni yako ni 20% ya mapato ya Kinjy kutokana na wanachama uliowadhamini, na kila kiasi kinarekodiwa kwenye ledger. Unaweza kutoa salio lako likifika $10, ada ya mtandao ikiwemo, ukiwa umethibitishwa utambulisho na umeongeza na kuthibitisha mkoba unaotaka kulipwa. Malipo hutumwa kwa USDT kwenye BSC; ada ya mtandao huonyeshwa kabla ya kutoa na hukatwa kwenye kiasi kinachotumwa. Chini ya $10, salio hubaki kwenye mkoba wako hadi lifikie kiwango cha chini.',
      fr: 'Votre commission est de 20 % du revenu de Kinjy sur ce que font les membres que vous avez parrainés, et chaque montant est inscrit au registre. Vous pouvez retirer dès que votre solde disponible atteint 10 $, frais de réseau compris, une fois votre identité vérifiée et le portefeuille où être payé ajouté et confirmé. Les retraits sont envoyés en USDT sur BSC ; les frais de réseau sont affichés avant le retrait et déduits du montant envoyé. En dessous de 10 $, votre solde reste dans votre portefeuille jusqu’au minimum.',
      ar: 'عمولتك هي 20% من إيرادات Kinjy على ما يقوم به الأعضاء الذين رعيتهم، ويُسجَّل كل مبلغ في السجل المحاسبي. يمكنك السحب عندما يبلغ رصيدك المتاح 10 دولارات شاملةً رسوم الشبكة، بعد التحقق من هويتك وإضافة المحفظة التي تريد استلام المال عليها وتأكيدها. تُرسَل المدفوعات بعملة USDT على شبكة BSC، وتُعرض رسوم الشبكة قبل السحب وتُخصم من المبلغ المرسل. ما دون 10 دولارات، يبقى رصيدك في محفظتك حتى يبلغ الحد الأدنى.',
      zh: '您的佣金为您推荐的会员所产生的 Kinjy 收入的 20%，每一笔都记录在账本中。当可用余额达到 10 美元（含网络手续费），且您已完成身份验证并添加、确认了收款钱包后，即可提现。提现以 BSC 网络上的 USDT 发送；网络手续费会在提现前显示，并从发送金额中扣除。低于 10 美元时，余额会保留在您的钱包中，直到达到最低额度。',
    },
    steps: [
      'Earn commission — every amount is recorded in the ledger as it is earned.',
      'Verify your identity and add the wallet you want to be paid to.',
      'Once your available balance reaches $10, press Withdraw in Payments.',
    ],
    deepLink: { to: '/payments', label: 'Open Payments' },
  },
  {
    id: 'payout-eligibility',
    title: { en: 'When you can withdraw (KYC + wallet + $10)', sw: 'Lini unaweza kutoa (KYC + mkoba + $10)', fr: 'Quand vous pouvez retirer (KYC + portefeuille + 10 $)', ar: 'متى يمكنك السحب (التحقق + المحفظة + 10 دولارات)', zh: '何时可以提现（实名认证 + 钱包 + 10 美元）' },
    module: 'Payments guide',
    version: 'v2.17.0',
    roles: ['member', 'admin'],
    keywords: ['eligibility', 'qualify', 'verify', 'verified', 'wallet address', 'backoffice', 'kyc', 'withdraw', 'ustahiki', 'éligibilité', 'أهلية', 'محفظة', '资格', '钱包地址', '实名'],
    answer: {
      en: 'The Withdraw button in Payments unlocks when three things are in place: your identity is verified through KinjyKYC, the wallet you want to be paid to (BSC) is added and confirmed, and your available balance is at least $10, network fee included. Until then you keep earning normally, and Payments shows what is still missing.',
      sw: 'Kitufe cha Kutoa kwenye Malipo hufunguka mambo matatu yakikamilika: utambulisho wako umethibitishwa kupitia KinjyKYC, mkoba unaotaka kulipwa (BSC) umeongezwa na kuthibitishwa, na salio lako linalopatikana ni angalau $10, ada ya mtandao ikiwemo. Hadi hapo unaendelea kupata kama kawaida, na ukurasa wa Malipo unaonyesha kinachokosekana.',
      fr: 'Le bouton Retirer de Paiements se débloque quand trois conditions sont réunies : votre identité vérifiée via KinjyKYC, le portefeuille où vous voulez être payé (BSC) ajouté et confirmé, et un solde disponible d’au moins 10 $, frais de réseau compris. En attendant, vous continuez de gagner normalement, et Paiements indique ce qui manque encore.',
      ar: 'يُفتح زر السحب في صفحة المدفوعات عند توفر ثلاثة أمور: التحقق من هويتك عبر KinjyKYC، وإضافة المحفظة التي تريد استلام المال عليها (BSC) وتأكيدها، وأن يبلغ رصيدك المتاح 10 دولارات على الأقل شاملةً رسوم الشبكة. وحتى ذلك الحين تواصل الكسب بشكل طبيعي، وتعرض صفحة المدفوعات ما لا يزال ناقصًا.',
      zh: '当三项条件都满足时，“付款”中的提现按钮才会解锁：通过 KinjyKYC 完成身份验证、添加并确认收款钱包（BSC）、可用余额至少 10 美元（含网络手续费）。在此之前，您的收益照常累计，“付款”页面会显示仍缺少的内容。',
    },
    deepLink: { to: '/payments', label: 'Check my eligibility' },
  },
  {
    id: 'marketplace-finality',
    title: { en: 'Marketplace: when a purchase becomes final', sw: 'Soko: ununuzi unapokamilika', fr: 'Marché : quand un achat devient définitif', ar: 'السوق: متى يصبح الشراء نهائيًا', zh: '市场：购买何时成为最终' },
    module: 'Marketplace guide',
    version: 'v2.17.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['marketplace', 'held', 'confirm receipt', 'final', 'delivery', 'refund', 'warranty', 'guarantee', 'insurance', 'ebook download', 'seller', 'buyer', 'dispute', 'thibitisha', 'définitif', 'garantie', 'نهائي', 'ضمان', '确认收货', '保修'],
    answer: {
      en: 'Your payment for a marketplace order is held until the purchase becomes final. For a product or a service, it becomes final when you confirm receipt in your own Kinjy account — confirm only when the right product is in front of you, inspected and ready to be handed over, or the service has been performed as described. Your confirmation immediately starts the seller’s settlement, and the seller checks it in their own back office before handing over. For an ebook, your first successful download makes the purchase final. A deadline, a seller marking an order delivered or a courier never releases the payment. Kinjy does not arbitrate ordinary marketplace disputes: after the purchase is final, questions about quality, repair, replacement or warranty go to the seller — or the insurer, when the listing includes insurance — subject to the consumer rights that apply to you. Each listing states whether a guarantee, warranty or insurance exists before you pay. Kinjy support still helps with failed or duplicate payments, a product not handed over after confirmation, fraud reports and legally required action.',
      sw: 'Malipo yako ya oda ya sokoni hushikiliwa hadi ununuzi ukamilike. Kwa bidhaa au huduma, unakamilika unapothibitisha kupokea kwenye akaunti yako ya Kinjy — thibitisha tu bidhaa sahihi ikiwa mbele yako, umeikagua na iko tayari kukabidhiwa, au huduma imefanyika kama ilivyoelezwa. Uthibitisho wako huanzisha mara moja malipo kwa muuzaji, na muuzaji huukagua kwenye ofisi yake kabla ya kukabidhi. Kwa ebook, upakuaji wako wa kwanza uliofanikiwa hukamilisha ununuzi. Muda kupita, muuzaji kuandika kuwa ameleta au msafirishaji hawatoi malipo kamwe. Kinjy haisuluhishi migogoro ya kawaida ya sokoni: baada ya ununuzi kukamilika, maswali ya ubora, ukarabati, ubadilishaji au dhamana huenda kwa muuzaji — au kwa bima, tangazo likijumuisha bima — kwa kuzingatia haki za mlaji zinazokuhusu. Kila tangazo linaeleza kama kuna dhamana au bima kabla hujalipa. Msaada wa Kinjy bado husaidia kwa malipo yaliyoshindwa au kurudiwa, bidhaa isiyokabidhiwa baada ya uthibitisho, ripoti za udanganyifu na hatua zinazotakiwa kisheria.',
      fr: 'Votre paiement pour une commande du marché est conservé jusqu’à ce que l’achat devienne définitif. Pour un produit ou un service, il le devient quand vous confirmez la réception depuis votre propre compte Kinjy — ne confirmez que lorsque le bon produit est devant vous, inspecté et prêt à vous être remis, ou que le service a été réalisé comme décrit. Votre confirmation lance immédiatement le règlement du vendeur, qui la vérifie dans son propre back-office avant la remise. Pour un ebook, votre premier téléchargement réussi rend l’achat définitif. Un délai écoulé, un vendeur qui marque la commande livrée ou un transporteur ne libèrent jamais le paiement. Kinjy n’arbitre pas les litiges ordinaires du marché : une fois l’achat définitif, les questions de qualité, réparation, remplacement ou garantie se règlent avec le vendeur — ou l’assureur si l’annonce inclut une assurance — sous réserve des droits des consommateurs qui vous sont applicables. Chaque annonce indique avant le paiement s’il existe une garantie ou une assurance. Le support Kinjy aide toujours en cas de paiement échoué ou en double, de produit non remis après confirmation, de signalement de fraude et d’action requise par la loi.',
      ar: 'يُحتفظ بدفعتك لطلب من السوق حتى يصبح الشراء نهائيًا. بالنسبة لمنتج أو خدمة، يصبح نهائيًا عندما تؤكد الاستلام من حسابك الخاص على Kinjy — لا تؤكد إلا عندما يكون المنتج الصحيح أمامك، وقد فحصته وهو جاهز للتسليم، أو عندما تكون الخدمة قد أُنجزت كما وُصفت. يبدأ تأكيدك فورًا تسوية مستحقات البائع، ويتحقق البائع منه في مكتبه الخلفي قبل التسليم. أما الكتاب الإلكتروني، فإن أول تنزيل ناجح له يجعل الشراء نهائيًا. لا يؤدي انقضاء مهلة أو وضع البائع علامة التسليم أو شركة الشحن أبدًا إلى تحرير الدفعة. لا تحكم Kinjy في النزاعات العادية في السوق: بعد أن يصبح الشراء نهائيًا، تُوجَّه أسئلة الجودة والإصلاح والاستبدال والضمان إلى البائع — أو إلى شركة التأمين إذا تضمن الإعلان تأمينًا — مع مراعاة حقوق المستهلك السارية عليك. يوضح كل إعلان قبل الدفع ما إذا كان هناك ضمان أو تأمين. ويظل دعم Kinjy يساعد في حالات الدفع الفاشل أو المكرر، وعدم تسليم المنتج بعد التأكيد، والإبلاغ عن الاحتيال، والإجراءات التي يفرضها القانون.',
      zh: '您为市场订单支付的款项会被保留，直到购买成为最终。对于商品或服务，当您在自己的 Kinjy 账户中确认收货时即成为最终——请仅在正确的商品就在您面前、已检查并准备交付，或服务已按描述完成时再确认。您的确认会立即启动卖家的结算，卖家在交付前会在自己的后台核实该确认。对于电子书，您的首次成功下载即使购买成为最终。期限届满、卖家标记已送达或快递员都不会释放款项。Kinjy 不仲裁普通的市场纠纷：购买成为最终后，质量、维修、更换或保修问题请联系卖家——如商品包含保险则联系保险公司——并以适用于您的消费者权利为准。每个商品在付款前都会说明是否提供保证、保修或保险。对于付款失败或重复扣款、确认后未交付商品、欺诈举报以及法律要求的事项，Kinjy 客服仍会提供帮助。',
    },
    steps: [
      'Read the listing: whether a guarantee, warranty or insurance is included is stated before you pay.',
      'Pay; the payment is held. Inspect the product (or the completed service) at handover.',
      'Confirm receipt in your own account only at that moment — for an ebook, your first successful download makes it final.',
    ],
    deepLink: { to: '/commerce', label: 'Open Marketplace' },
  },
  {
    id: 'onboarding-concierge',
    title: { en: 'Onboarding Concierge', sw: 'Msaidizi wa Kuanza', fr: 'Concierge d’accueil', ar: 'مرشد الانضمام', zh: '新手引导助手' },
    module: 'App guide',
    version: 'v2.15.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['onboarding', 'getting started', 'new member', 'setup', 'concierge', 'kuanza', 'mwanzo', 'bienvenue', 'انضمام', '新手', '入门'],
    answer: {
      en: 'The AI Onboarding Concierge interviews you when you join — interests, languages, city, goals — then builds your starter Kinjy automatically: first Circles, your chosen algorithm, feed-mode defaults, step 1 of your Family Tree, and even a first drafted post. Try the live demo inside the App page.',
      sw: 'Msaidizi wa Kuanza wa AI hukuuliza maswali unapojiunga — maslahi, lugha, mji, malengo — kisha kujenga Kinjy yako ya mwanzo: Circles za kwanza, algorithm, mipangilio ya mlisho, hatua ya kwanza ya Mti wa Familia, na chapisho la kwanza.',
      fr: 'Le Concierge d’accueil IA vous interviewe à votre arrivée — centres d’intérêt, langues, ville, objectifs — puis construit votre Kinjy de départ : premiers Cercles, algorithme choisi, modes de fil par défaut, première étape de votre arbre familial et même une première publication rédigée.',
      ar: 'يقوم مرشد الانضمام الذكي بمقابلتك عند التسجيل — الاهتمامات واللغات والمدينة والأهداف — ثم يبني انطلاقتك تلقائيًا: دوائرك الأولى، خوارزميتك المختارة، أوضاع الخلاصة الافتراضية، الخطوة الأولى من شجرة عائلتك، وحتى مسودة منشورك الأول.',
      zh: 'AI 新手引导助手会在您加入时与您对话——兴趣、语言、城市、目标——然后自动为您搭建初始体验：首批圈子、所选算法、信息流默认设置、家族树第一步，甚至为您起草第一条帖子。',
    },
    deepLink: { to: '/app', label: 'Try the concierge demo' },
  },
  {
    id: 'heritage-interview',
    title: { en: 'Heritage Interview Agent', sw: 'Wakala wa Mahojiano ya Urithi', fr: 'Agent d’interview patrimoine', ar: 'وكيل مقابلات التراث', zh: '家族传承访谈助手' },
    module: 'Family guide',
    version: 'v2.15.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['interview', 'elder', 'story', 'record story', 'heritage interview', 'oral history', 'mahojiano', 'mzee', 'interview', 'ainé', 'مقابلة', 'كبار السن', '访谈', '长辈'],
    answer: {
      en: 'The Heritage Interview Agent holds a gentle guided conversation with an elder in their own language, records it, transcribes it, and files the structured answers into your Family Heritage Archive — linked to their Person Record and family timeline. The original recording is always preserved byte-for-byte; the transcript is labeled AI Assisted, and every claim cites the recording. AI never invents history.',
      sw: 'Wakala wa Mahojiano ya Urithi hufanya mazungumzo ya kuongozwa na mzee kwa lugha yake, kurekodi, kunakili, na kuhifadhi majibu kwenye Kumbukumbu ya Urithi wa Familia — ukihusisha na Rekodi yake ya Mtu na ratiba ya familia. Rekodi asili huhifadhiwa daima; nakili huwekewa lebo AI Assisted, na kila dai linataja rekodi. AI haitungi historia.',
      fr: 'L’Agent d’interview patrimoine mène une conversation guidée et bienveillante avec un aîné dans sa langue, l’enregistre, la transcrit et classe les réponses structurées dans vos Archives familiales — liées à sa fiche et à la chronologie. L’enregistrement original est toujours préservé à l’identique ; la transcription est étiquetée IA Assistée, et chaque fait cite l’enregistrement. L’IA n’invente jamais l’histoire.',
      ar: 'يجري وكيل مقابلات التراث محادثة موجّهة لطيفة مع كبير في السن بلغته، ويسجلها وينسخها ويحفظ الإجابات المنظمة في أرشيف التراث العائلي — مربوطة بسجله الشخصي والجدول الزمني للعائلة. يُحفظ التسجيل الأصلي دائمًا كما هو؛ والنص المنسوخ موسوم بـ"بمساعدة الذكاء الاصطناعي"، وكل معلومة تُنسب إلى التسجيل. الذكاء الاصطناعي لا يختلق التاريخ أبدًا.',
      zh: '家族传承访谈助手会用长辈的母语与其进行温和的引导式对话，录音、转写，并将整理好的内容归档到家族传承档案馆——与其个人档案和家族时间线关联。原始录音始终逐字节保存；转写文本标注为"AI 辅助"，每处内容均引用录音来源。AI 绝不虚构历史。',
    },
    image: '/assistant-illustration-2.jpg',
    imageAlt: 'Heritage interview filed into the family archive',
    deepLink: { to: '/family', label: 'Meet the Interview Agent' },
  },
  {
    id: 'prepublish-guardian',
    title: { en: 'Pre-Publish Guardian', sw: 'Mlinzi wa Kabla ya Kuchapisha', fr: 'Gardien pré-publication', ar: 'حارس ما قبل النشر', zh: '发布前守护' },
    module: 'Creator guide',
    version: 'v2.15.0',
    roles: ['member', 'admin'],
    keywords: ['pre-publish', 'guardian', 'flag', 'rule', 'violation', 'will this be flagged', 'mlinzi', 'avant publication', 'قبل النشر', '发布前', '违规'],
    answer: {
      en: 'Before you publish, the Pre-Publish Guardian scans your draft and tells you plainly: whether it is likely to be flagged, the exact rule (e.g. Rule 4.2 — unverified health claim), why, and a suggested fix. Apply the edit and you get a green "Safe to publish". Moderation that helps you publish — not strike you after.',
      sw: 'Kabla ya kuchapisha, Mlinzi wa Kabla ya Kuchapisha huchunguza rasimu yako na kukuambia wazi: kama inaweza kuashiria, kanuni halisi (k.m. Kanuni 4.2 — dai la afya lisilothibitishwa), kwa nini, na mapendekezo ya kurekebisha. Fanya marekebisho upate "Salama kuchapisha".',
      fr: 'Avant de publier, le Gardien pré-publication analyse votre brouillon et vous dit clairement s’il risque d’être signalé, la règle exacte (ex. Règle 4.2 — allégation santé non vérifiée), pourquoi, et propose une correction. Appliquez-la et obtenez le feu vert « Publication sûre ».',
      ar: 'قبل النشر، يفحص حارس ما قبل النشر مسودتك ويخبرك بوضوح: هل من المرجح الإبلاغ عنها، والقاعدة المحددة (مثل القاعدة 4.2 — ادعاء صحي غير موثق)، ولماذا، مع اقتراح تصحيح. طبّق التعديل وتحصل على "آمن للنشر".',
      zh: '发布前，"发布前守护"会扫描您的草稿并明确告知：是否可能被标记、具体违反的规则（如规则 4.2——未经验证的健康声明）、原因以及修改建议。应用修改后即可获得绿色的"可以安全发布"状态。',
    },
    deepLink: { to: '/creators', label: 'See the Guardian' },
  },
  {
    id: 'wellbeing',
    title: { en: 'Wellbeing & Session Intelligence', sw: 'Ustawi na Akili ya Kipindi', fr: 'Bien-être et intelligence de session', ar: 'الرفاه وذكاء الجلسة', zh: '健康与会话智能' },
    module: 'App guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['wellbeing', 'session intelligence', 'scroll', 'break', 'balance score', 'ustawi', 'pumziko', 'bien-être', 'الرفاه', 'استراحة', '健康', '屏幕时间'],
    answer: {
      en: 'Wellbeing & Session Intelligence is an opt-in panel that notices continuous-scroll patterns — like 42 minutes without a pause — and responds with a gentle intervention card suggesting a switch to the Positive Content algorithm or a short break. A personal wellbeing dashboard tracks your balance score over time so you can see how your sessions trend. It is private by design: the analysis is for you alone, and no wellbeing data is shared with other members or advertisers.',
      sw: 'Ustawi na Akili ya Kipindi ni paneli ya kujiunga inayotambua mifumo ya kusoma bila mapumziko — kama dakika 42 mfululizo — na kujibu kwa kadi laini inayopendekeza ubadilishe kwa algorithm ya Positive Content au upumzike kidogo. Dashibodi yako binafsi ya ustawi hufuatilia alama yako ya usawa kwa muda ili uone mwenendo wa vipindi vyako. Ni ya faragha kwa muundo: uchambuzi ni wako pekee, na hakuna data ya ustawi inayoshirikiwa na wanachama wengine au watangazaji.',
      fr: 'Bien-être et intelligence de session est un panneau optionnel qui détecte les schémas de défilement continu — 42 minutes sans pause, par exemple — et répond par une carte d’intervention douce suggérant de passer à l’algorithme Positive Content ou de souffler un peu. Un tableau de bord personnel suit votre score d’équilibre dans le temps. C’est privé par conception : l’analyse est pour vous seul, et aucune donnée de bien-être n’est partagée avec d’autres membres ou des annonceurs.',
      ar: 'الرفاه وذكاء الجلسة لوحة اختيارية ترصد أنماط التمرير المتواصل — مثل 42 دقيقة دون توقف — وتستجيب ببطاقة تدخل لطيفة تقترح التبديل إلى خوارزمية المحتوى الإيجابي أو أخذ استراحة قصيرة. وتتبع لوحة الرفاه الشخصية درجة توازنك عبر الزمن لترى اتجاه جلساتك. وهي خاصة بالتصميم: التحليل لك وحدك، ولا تُشارك بيانات الرفاه مع الأعضاء الآخرين أو المعلنين.',
      zh: '健康与会话智能是一个自愿开启的面板，能识别连续刷屏行为——例如连续 42 分钟没有停顿——并以温和的干预卡片回应，建议您切换到"正能量"算法或稍作休息。个人健康仪表盘会持续追踪您的平衡分数，让您看清自己的使用趋势。它在设计上就是私密的：分析只为您服务，任何健康数据都不会与其他会员或广告商共享。',
    },
    steps: [
      'Open the App page and enable the Wellbeing panel — it is strictly opt-in.',
      'When the intervention card appears, tap "Switch to Positive Content" or "Take a break".',
      'Review your balance score trend on the personal wellbeing dashboard.',
    ],
    image: '/assistant-illustration-2.jpg',
    imageAlt: 'Wellbeing intervention card with balance score',
    deepLink: { to: '/app', label: 'Open the App' },
  },
  {
    id: 'data-saver',
    title: { en: 'Offline-First & Data Saver', sw: 'Offline-First na Kuokoa Data', fr: 'Hors-ligne et économie de données', ar: 'وضع عدم الاتصال وتوفير البيانات', zh: '离线优先与省流量模式' },
    module: 'App guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['data saver', 'offline', 'low bandwidth', 'text-first', 'tap to load', 'offline queue', 'sms', 'ussd', 'okoa data', 'hors ligne', 'توفير البيانات', 'دون اتصال', '省流量', '离线'],
    answer: {
      en: 'Low-Bandwidth Mode makes Kinjy offline-first. Flip the Data Saver toggle and the feed turns text-first: media collapses to tap-to-load placeholders that show the exact byte size before you spend it, and audio-first posts keep conversations flowing on thin connections. Posts you write offline sit in a compose queue — "2 posts queued — will send when online" — and an SMS/USSD fallback keeps the essentials reachable with no data at all.',
      sw: 'Hali ya Data Saver huifanya Kinjy kuwa offline-first. Washa Data Saver na mlisho hugeuka kuwa wa maandishi kwanza: media hujikunja kuwa tap-to-load ikionyesha ukubwa halisi wa byte kabla hujatumia, na machapisho ya audio-first huendeleza mazungumzo kwenye mtandao dhaifu. Machapisho uliyoandika nje ya mtandao huingia kwenye foleni — "machapisho 2 yako foleni — yatatumwa mtandaoni" — na njia ya SMS/USSD huweka mambo muhimu yanafikiwa bila data kabisa.',
      fr: 'Le mode faible débit rend Kinjy hors-ligne d’abord. Activez l’économiseur de données et le fil devient texte d’abord : les médias se replient en vignettes à charger d’un toucher, avec leur taille exacte en octets avant de la dépenser, et les publications audio-first gardent la conversation vivante sur les connexions lentes. Vos brouillons hors ligne rejoignent une file — « 2 publications en attente — envoi au retour en ligne » — et le repli SMS/USSD garde l’essentiel accessible sans aucune donnée.',
      ar: 'وضع النطاق المنخفض يجعل كالوتا تعمل دون اتصال أولًا. فعّل موفّر البيانات فيتحول الملخص إلى نص أولًا: تنطوي الوسائط إلى عناصر تُحمَّل بالنقر مع عرض حجمها الدقيق بالبايت قبل أن تنفقه، وتحافظ المنشورات الصوتية على تدفق المحادثات عبر الاتصالات الضعيفة. وتنتظر مسوداتك في قائمة إرسال — "منشوران في الانتظار — سيُرسلان عند عودة الاتصال" — ويوفر خيار SMS/USSD الوصول إلى الأساسيات دون أي بيانات.',
      zh: '低带宽模式让 Kinjy 实现离线优先。打开"省流量"开关后，信息流变为文字优先：媒体折叠为点按加载的占位符，并在您花费流量前显示确切字节大小；音频优先的帖子让对话在弱网下也能继续。离线时写好的帖子会进入发送队列——"2 条帖子排队中——联网后自动发送"——而 SMS/USSD 备用通道确保在完全没有流量时也能使用核心功能。',
    },
    steps: [
      'Toggle Data Saver in the feed header — media becomes tap-to-load with byte sizes.',
      'Compose while offline; your posts enter the send queue automatically.',
      'No data at all? Use the SMS/USSD fallback for the essentials.',
    ],
    deepLink: { to: '/app', label: 'Try Data Saver' },
  },
  {
    id: 'series',
    title: { en: 'Serialized Content', sw: 'Maudhui ya Mfululizo', fr: 'Contenus en séries', ar: 'المحتوى المتسلسل', zh: '连载内容' },
    module: 'App guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['series', 'episode', 'episode 4 of 12', 'next episode', 'serialized', 'streak', 'mfululizo', 'kipindi', 'série', 'épisode', 'مسلسل', 'حلقة', '连载', '剧集'],
    answer: {
      en: 'Serialized Content turns great posts into binge-worthy series. The series rail shows exactly where you are — "Episode 4 of 12" — with next-episode play and a notification whenever a new episode drops from a series you follow. Creators earn streak badges for consistent publishing, and readers never lose their place.',
      sw: 'Maudhui ya Mfululizo hubadilisha machapisho mazuri kuwa mfululizo wa kufuatilia. Reli ya mfululizo inaonyesha hasa ulipo — "Kipindi 4 kati ya 12" — na uchezaji wa kipindi kinachofuata pamoja na arifa kila kipindi kipya kikitoka kutoka kwa mfululizo unaoufuatilia. Watayarishaji hupata beji za mfululizo kwa kuchapisha kwa nidhamu, na wasomaji hawaupotezi mahali walipo.',
      fr: 'Les contenus en séries transforment vos meilleures publications en feuilletons à suivre. Le rail de série indique précisément où vous en êtes — « Épisode 4 sur 12 » — avec lecture de l’épisode suivant et notification à chaque nouvel épisode d’une série suivie. Les créateurs gagnent des badges de régularité, et les lecteurs ne perdent jamais leur place.',
      ar: 'المحتوى المتسلسل يحوّل المنشورات الرائعة إلى سلاسل تستحق المتابعة. يعرض شريط السلسلة موضعك بدقة — "الحلقة 4 من 12" — مع تشغيل الحلقة التالية وإشعار عند صدور حلقة جديدة من سلسلة تتابعها. ويحصل المبدعون على شارات الاستمرارية لالتزامهم بالنشر، ولن يضيع القراء مكانهم أبدًا.',
      zh: '连载内容让优质帖子变成值得追更的系列。系列进度条清晰显示您的位置——"第 4 集，共 12 集"——支持自动播放下一集，并在您关注的系列更新时推送通知。创作者坚持更新可获得连更徽章，读者也永远不会丢失进度。',
    },
    deepLink: { to: '/app', label: 'Explore series' },
  },
  {
    id: 'vault-resurfacing',
    title: { en: 'AI Memory Resurfacing', sw: 'Kufufua Kumbukumbu kwa AI', fr: 'Resurgissement de la mémoire IA', ar: 'استرجاع الذاكرة بالذكاء الاصطناعي', zh: 'AI 记忆唤回' },
    module: 'App guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['vault', 'resurfacing', 'memory', 'saved item', 'knowledge vault', 'trending', 'kumbukumbu', 'mémoire', 'coffre', 'ذاكرة', 'استرجاع', '记忆', '唤回', '知识库'],
    answer: {
      en: 'The Knowledge Vault does not just store what you save — it resurfaces it when it matters. If a discussion trends that relates to something you saved months ago — say, the cassava farming proposal you filed six months back and a trending Kigoma Forum thread — the Vault brings it back to the top with its context. Every resurfaced card offers Open, View, Dismiss and Undo, so the memory works for you, never against you.',
      sw: 'Kumbukumbu (Vault) haituhifadhi tu ulichohifadhi — hulirudisha linapohitajika. Ikiwa mjadala unaovuma unahusiana na kitu ulichohifadhi miezi iliyopita — kama pendekezo lako la kilimo cha mihogo uliloweka miezi sita iliyopita na mjadala unaovuma wa Kigoma Forum — Vault hulirudisha juu na muktadha wake. Kila kadi inayorudishwa ina vitufe vya Open, View, Dismiss na Undo, hivyo kumbukumbu inakufanyia kazi wewe, si dhidi yako.',
      fr: 'Le coffre de connaissances ne se contente pas de stocker vos sauvegardes — il les fait resurgir au bon moment. Si une discussion tendance touche à un élément sauvegardé il y a des mois — par exemple votre projet de culture du manioc archivé il y a six mois et un fil du Forum de Kigoma en vogue — le coffre le remonte avec son contexte. Chaque carte propose Ouvrir, Voir, Ignorer et Annuler : la mémoire travaille pour vous, jamais contre vous.',
      ar: 'خزانة المعرفة لا تخزّن ما تحفظه فحسب — بل تعيد إظهاره عندما يهم. إذا انتشر نقاش يتصل بشيء حفظته قبل أشهر — كاقتراحك عن زراعة الكاسافا المؤرشف قبل ستة أشهر وموضوع رائج في منتدى كيغوما — تُعيده الخزانة إلى الواجهة مع سياقه. وكل بطاقة مسترجعة تتيح الفتح والعرض والتجاهل والتراجع، فالذاكرة تعمل لصالحك لا ضدك.',
      zh: '知识库不仅保存您收藏的内容，还会在相关时刻主动唤回。如果某个热门讨论与您数月前保存的内容相关——比如您六个月前归档的木薯种植提案，恰好遇上基戈马论坛的热帖——知识库会将其连同上下文重新置顶。每张唤回卡片都提供打开、查看、忽略和撤销操作，让记忆只为您服务。',
    },
    deepLink: { to: '/app', label: 'Open the Vault' },
  },
  {
    id: 'crisis-alerts',
    title: { en: 'Crisis & Community Alerts', sw: 'Tahadhari za Dharura na Jamii', fr: 'Alertes de crise et communautaires', ar: 'تنبيهات الأزمات والمجتمع', zh: '危机与社区警报' },
    module: 'Safety guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['crisis', 'alert', 'flood', 'i am safe', 'check-in', 'emergency', 'dharura', 'tahadhari', 'crise', 'alerte', 'أزمة', 'تنبيه', '危机', '警报'],
    answer: {
      en: 'Crisis & Community Alert Mode pushes verified geographic alerts — like a flood warning in Kigoma — to the members actually in the affected area. One tap sends an "I’m safe" check-in, with a live counter of how many have checked in and automatic notification of your family circle. Alerts are distributed through city, district and neighborhood feeds, so the signal reaches people at the right granularity.',
      sw: 'Hali ya Tahadhari za Dharura husambaza tahadhari zilizothibitishwa kijiografia — kama onyo la mafuriko Kigoma — kwa wanachama walioko kweli eneo lililoathirika. Bofya mara moja kutuma ujumbe wa "Niko salama", na kiasi cha moja kwa moja cha waliothibitisha usalama pamoja na arifa otomatiki kwa duru yako ya familia. Tahadhari husambazwa kupitia milisho ya mji, wilaya na mtaa, ili ishara ifike kwa watu kwa kina sahihi.',
      fr: 'Le mode Alerte de crise diffuse des alertes géographiques vérifiées — comme une alerte inondation à Kigoma — aux membres réellement présents dans la zone touchée. Un seul geste envoie un « Je suis en sécurité », avec compteur en direct des personnes enregistrées et notification automatique de votre cercle familial. Les alertes passent par les fils de ville, de district et de quartier, pour toucher chacun à la bonne échelle.',
      ar: 'وضع تنبيهات الأزمات يوجّه تنبيهات جغرافية موثقة — كتحذير من فيضان في كيغوما — إلى الأعضاء الموجودين فعليًا في المنطقة المتضررة. وبنقرة واحدة ترسل تأكيد "أنا بخير" مع عدّاد مباشر لمن سجّلوا سلامتهم وإشعار تلقائي لدائرة عائلتك. وتُوزَّع التنبيهات عبر خلاصات المدينة والمنطقة والحي لتصل إلى الناس بالدقة المناسبة.',
      zh: '危机与社区警报模式会将经过核实的地理警报——例如基戈马的洪水预警——精准推送给真正身处灾区的会员。一键即可发送"我很安全"签到，实时显示已确认安全的人数，并自动通知您的家庭圈。警报通过城市、区县和社区三级信息流分发，确保信息以恰当的粒度触达。',
    },
    steps: [
      'When a verified alert covers your area, it appears at the top of your local feeds.',
      'Tap "I’m safe" — your family circle is notified and the counter updates live.',
      'Follow the city, district or neighborhood feed for the granularity you need.',
    ],
    image: '/assistant-illustration-2.jpg',
    imageAlt: 'Crisis alert with I’m safe check-in counter',
    deepLink: { to: '/safety', label: 'Open Safety Center' },
  },
  {
    id: 'c2pa-signing',
    title: { en: 'C2PA Content Credentials', sw: 'Hati za Maudhui za C2PA', fr: 'Identifiants de contenu C2PA', ar: 'بيانات اعتماد المحتوى C2PA', zh: 'C2PA 内容凭证' },
    module: 'Safety guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['c2pa', 'provenance', 'content credentials', 'signed', 'manifest', 'synthetic media', 'deepfake', 'hati', 'origine', 'مصدر', 'بيانات الاعتماد', 'التوقيع', '内容凭证', '溯源'],
    answer: {
      en: 'Every photo and video captured in Kinjy is signed with C2PA content credentials at the moment of capture, and those credentials are preserved through the entire edit chain. Anyone can open the signed manifest — issuer, timestamp, hash and signature — and verify for themselves where a piece of media came from. It is Kinjy’s answer to the synthetic-media era: provenance labels you can check, not promises you must trust.',
      sw: 'Kila picha na video inayopigwa ndani ya Kinjy huwekewa saini ya hati za maudhui za C2PA wakati wa kupiga, na hati hizo huhifadhiwa kupitia mnyororo wote wa uhariri. Mtu yeyote anaweza kufungua manifest iliyosainiwa — mtoaji, muda, hash na saini — na kuthibitisha mwenyewe chanzo cha kipande cha media. Hii ndiyo jibu la Kinjy kwa enzi ya synthetic media: lebo za chanzo unazoweza kukagua, si ahadi unazopaswa kuamini.',
      fr: 'Chaque photo et vidéo capturée dans Kinjy est signée avec des identifiants de contenu C2PA au moment de la capture, et ces identifiants sont préservés tout au long de la chaîne de montage. N’importe qui peut ouvrir le manifeste signé — émetteur, horodatage, empreinte et signature — et vérifier l’origine d’un média. C’est la réponse de Kinjy à l’ère des médias synthétiques : des labels de provenance vérifiables, pas des promesses à croire.',
      ar: 'كل صورة وفيديو يُلتقط داخل كالوتا يُوقَّع ببيانات اعتماد محتوى C2PA لحظة الالتقاط، وتُحفظ هذه البيانات عبر سلسلة التحرير بأكملها. ويمكن لأي شخص فتح البيان الموقّع — الجهة المصدرة والطابع الزمني والبصمة والتوقيع — والتحقق بنفسه من مصدر الوسائط. هذا هو رد كالوتا على عصر الوسائط الاصطناعية: ملصقات مصدر يمكن فحصها، لا وعود يجب تصديقها.',
      zh: '在 Kinjy 内拍摄的每张照片和每段视频都会在拍摄瞬间签署 C2PA 内容凭证，并在整个编辑链路中完整保留。任何人都可以打开已签名的清单——签发者、时间戳、哈希值和签名——亲自核实媒体的来源。这是 Kinjy 面对合成媒体时代的答案：可验证的溯源标签，而非只能轻信的承诺。',
    },
    deepLink: { to: '/safety', label: 'See provenance' },
  },
  {
    id: 'voice-first',
    title: { en: 'Voice-First Navigation', sw: 'Uabiraji wa Sauti Kwanza', fr: 'Navigation voix d’abord', ar: 'التنقل بالصوت أولًا', zh: '语音优先导航' },
    module: 'Safety guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['voice', 'voice command', 'read aloud', 'accessibility', 'dyslexia', 'audio description', 'sauti', 'accessibilité', 'voix', 'صوت', 'إمكانية الوصول', '语音', '无障碍', '朗读'],
    answer: {
      en: 'Kinjy works hands-free. Voice commands like "Open my Circles" or "Read this thread aloud" navigate the app for you, and AI audio descriptions narrate the content of visual posts for members who cannot see them. A dyslexia-friendly reading mode retypes any thread with accessible fonts and spacing. Accessibility is not a bolt-on here — it is the front door.',
      sw: 'Kinjy inafanya kazi bila kutumia mikono. Amri za sauti kama "Fungua Circles zangu" au "Nisomee mjadala huu" hukusogezea programu, na maelezo ya sauti ya AI hueleza maudhui ya machapisho ya kuona kwa wanachama wasioweza kuyaona. Hali ya kusoma rafiki kwa dyslexia huandika upya mjadala wowote kwa fonti na nafasi zinazosomeka. Upatikanaji si nyongeza hapa — ni mlango wa mbele.',
      fr: 'Kinjy fonctionne mains libres. Des commandes vocales comme « Ouvre mes Cercles » ou « Lis ce fil à voix haute » naviguent dans l’app pour vous, et les descriptions audio IA narrent le contenu des publications visuelles pour les membres qui ne peuvent pas les voir. Un mode de lecture adapté à la dyslexie remet en page n’importe quel fil avec polices et espacements accessibles. L’accessibilité n’est pas une option ici — c’est la porte d’entrée.',
      ar: 'كالوتا تعمل دون استخدام اليدين. أوامر صوتية مثل "افتح دوائري" أو "اقرأ هذا الموضوع بصوت عالٍ" تتنقل بالتطبيق نيابة عنك، والأوصاف الصوتية بالذكاء الاصطناعي تسرد محتوى المنشورات المرئية لمن لا يستطيعون رؤيتها. ويُعيد وضع القراءة الملائم لعسر القراءة تنضيد أي موضوع بخطوط وتباعد ميسّر. إمكانية الوصول هنا ليست إضافة لاحقة — بل هي الباب الأمامي.',
      zh: 'Kinjy 支持免手动操作。语音命令如"打开我的圈子"或"朗读这个帖子"可以替您导航应用，AI 音频描述还能为视障会员讲述图片帖子的内容。阅读障碍友好模式会用易读字体和间距重新排版任何帖子。无障碍在这里不是附加功能——它就是正门。',
    },
    deepLink: { to: '/safety', label: 'Accessibility features' },
  },
  {
    id: 'early-warning',
    title: { en: 'Community Early-Warning', sw: 'Onyo la Mapema la Jamii', fr: 'Alerte précoce communautaire', ar: 'الإنذار المبكر المجتمعي', zh: '社区早期预警' },
    module: 'Safety guide',
    version: 'v2.16.0',
    roles: ['member', 'admin'],
    keywords: ['early warning', 'moderator', 'sentiment', 'dispute', 'escalation', 'community health', 'sparkline', 'onyo la mapema', 'alerte précoce', 'إنذار مبكر', 'مشرف', '预警', '社区健康'],
    answer: {
      en: 'Community Early-Warning gives moderators foresight instead of hindsight. The dashboard shows a sentiment-drift sparkline per space, predicts dispute escalation — "80% likely to need intervention" — and recommends concrete actions before a thread boils over. Weekly community-health summaries keep the long trend in view, so moderation becomes prevention rather than cleanup.',
      sw: 'Onyo la Mapema la Jamii huwapa wasimamizi uwezo wa kuona mbeleni badala ya nyuma. Dashibodi inaonyesha mstari wa mabadiliko ya hisia kwa kila nafasi, kutabiri kuchochea kwa migogoro — "nafasi 80% ya kuhitaji uingiliaji" — na kupendekeza hatua mahususi kabla mjadala haujachacha. Muhtasari wa kila wiki wa afya ya jamii huweka mwenendo wa muda mrefu machoni, hivyo usimamizi hugeuka kuwa kuzuia badala ya kusafisha.',
      fr: 'L’alerte précoce donne aux modérateurs de la prévoyance plutôt que du recul. Le tableau de bord affiche une courbe de dérive des sentiments par espace, prédit l’escalade des conflits — « 80 % de chances d’intervention nécessaire » — et recommande des actions concrètes avant que le fil ne dégénère. Les résumés hebdomadaires de santé communautaire gardent la tendance longue en vue : la modération devient prévention, pas nettoyage.',
      ar: 'الإنذار المبكر يمنح المشرفين بصيرة استباقية بدل النظر إلى الخلف. تعرض اللوحة منحنى انجراف المشاعر لكل مساحة، وتتنبأ بتصاعد النزاعات — "احتمال 80% للحاجة إلى تدخل" — وتقترح إجراءات ملموسة قبل أن يشتعل النقاش. وتُبقي الملخصات الأسبوعية لصحة المجتمع الاتجاه الطويل في الأفق، فيتحول الإشراف إلى وقاية بدل تنظيف لاحق.',
      zh: '社区早期预警让版主拥有先见之明，而非事后补救。仪表盘为每个空间显示情绪漂移曲线，预测争端升级——"80% 可能性需要介入"——并在讨论失控之前给出具体处置建议。每周社区健康报告帮助把握长期趋势，让管理从善后变为预防。',
    },
    deepLink: { to: '/safety', label: 'Moderator dashboard' },
  },
  {
    id: 'family-year-review',
    title: { en: 'Family Year-in-Review', sw: 'Mapitio ya Mwaka wa Familia', fr: 'Rétrospective familiale de l’année', ar: 'مراجعة العام العائلية', zh: '家庭年度回顾' },
    module: 'Family guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['year in review', 'family documentary', 'reunion', 'reunion planner', 'memorial date', 'mapitio', 'rétrospective', 'réunion', 'مراجعة العام', 'لم الشمل', '年度回顾', '家庭聚会'],
    answer: {
      en: 'Family Year-in-Review auto-generates an annual family documentary from your tree growth, archive additions and the year’s milestones — a premium one-off keepsake the whole family can watch together. The companion Reunion Agent plans the gathering itself: it polls dates across generations, suggests venues near your family-map centroid and stays aware of memorial dates. From the year that was to the gathering ahead, in one place.',
      sw: 'Mapitio ya Mwaka wa Familia hutengeneza otomatiki filamu ya familia ya mwaka kutoka kwa ukuaji wa mti wako, nyaraka mpya na matukio muhimu ya mwaka — kumbukumbu ya premium inayotengenezwa mara moja na kutazamwa na familia nzima pamoja. Wakala wa Reunion hupanga mkusanyiko wenyewe: huchunguza tarehe zinazokubalika na vizazi vyote, kupendekeza mahali karibu na kitovu cha ramani ya familia yako, na kuzingatia tarehe za kumbukumbu.',
      fr: 'La rétrospective familiale génère automatiquement un documentaire annuel à partir de la croissance de votre arbre, des ajouts d’archives et des jalons de l’année — un souvenir premium, facturé une fois, que toute la famille regarde ensemble. L’agent Réunion planifie le rassemblement lui-même : il interroge les disponibilités de toutes les générations, propose des lieux proches du centre de votre carte familiale et tient compte des dates de commémoration.',
      ar: 'مراجعة العام العائلية تولّد تلقائيًا فيلمًا وثائقيًا سنويًا للعائلة من نمو شجرتك وإضافات الأرشيف وأبرز أحداث العام — تذكارًا مميزًا يُدفع مرة واحدة وتشاهده العائلة كلها معًا. ويخطط وكيل لمّ الشمل للتجمع نفسه: يستطلع التواريخ عبر الأجيال، ويقترح أماكن قريبة من مركز خريطة عائلتك، ويراعي تواريخ الذكرى.',
      zh: '家庭年度回顾会根据家族树的增长、档案新增内容和年度里程碑，自动生成一部年度家庭纪录片——一次性付费的高级珍藏品，全家可以一起观看。配套的聚会策划助手会筹办聚会本身：跨世代投票选出日期、推荐靠近家族地图中心的场地，并自动留意纪念日期。从刚过去的一年到即将到来的相聚，尽在同一个地方。',
    },
    steps: [
      'Open the Family page and request your Year-in-Review documentary (premium one-off).',
      'Let the Reunion Agent poll every generation for dates that work.',
      'Pick a venue near your family-map centroid; memorial dates are respected automatically.',
    ],
    image: '/assistant-illustration-1.jpg',
    imageAlt: 'Family year-in-review documentary and reunion planner',
    deepLink: { to: '/family', label: 'Open Family' },
  },
  {
    id: 'remembrance-gatherings',
    title: { en: 'Remembrance Gatherings', sw: 'Mikutano ya Ukumbusho', fr: 'Rassemblements du souvenir', ar: 'تجمعات الذكرى', zh: '追思聚会' },
    module: 'Family guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['remembrance', 'gathering', 'memorial', 'graveyard', 'attendance', 'ukumbusho', 'commémoration', 'rassemblement', 'ذكرى', 'تجمع', '追思', '纪念聚会'],
    answer: {
      en: 'Remembrance Gatherings turn memory into presence. From a memorial in the Digital Graveyard you can plan a family gathering — an anniversary, an unveiling or a quiet visit — with invitations and attendance tracking built in. They connect to the reunion planner, so the living calendar of the family always keeps room for those who came before.',
      sw: 'Mikutano ya Ukumbusho hubadilisha kumbukumbu kuwa uwepo. Kutoka kwenye kaburi la kidijitali unaweza kupanga mkusanyiko wa familia — kumbukumbu ya mwaka, ufunuo wa jiwe au ziara ya kimya — na mialiko na ufuatiliaji wa mahudhurio vimejengwa ndani. Inaunganishwa na mpangaji wa reunion, hivyo kalenda hai ya familia huweka nafasi daima kwa waliotangulia.',
      fr: 'Les rassemblements du souvenir transforment la mémoire en présence. Depuis un mémorial du cimetière numérique, planifiez un rassemblement familial — anniversaire, inauguration d’une stèle ou visite recueillie — avec invitations et suivi des présences intégrés. Connectés au planificateur de réunions, ils gardent une place, dans le calendrier vivant de la famille, à ceux qui nous ont précédés.',
      ar: 'تجمعات الذكرى تحوّل الذاكرة إلى حضور. من نُصب في المقبرة الرقمية يمكنك تخطيط تجمع عائلي — ذكرى سنوية أو إزاحة ستار أو زيارة هادئة — مع دعوات وتتبع للحضور مدمجين. وهي مرتبطة بمخطط لمّ الشمل، فتُبقي أجندة العائلة الحية مكانًا دائمًا لمن سبقونا.',
      zh: '追思聚会让思念化为相聚。您可以从数字纪念园中的纪念馆发起家庭聚会——忌日、揭碑仪式或安静的祭扫——内置邀请和出席追踪功能。它与聚会策划助手相连，让家庭日历永远为先人保留一席之地。',
    },
    deepLink: { to: '/memorials', label: 'Open Memorials' },
  },
  {
    id: 'live-intelligence',
    title: { en: 'Live Intelligence Layer', sw: 'Safu ya Akili ya Moja kwa Moja', fr: 'Couche d’intelligence en direct', ar: 'طبقة الذكاء المباشر', zh: '直播智能层' },
    module: 'Platform guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['live', 'captions', 'live room', 'question clustering', 'whisper', 'highlight', 'moja kwa moja', 'direct', 'sous-titres', 'بث مباشر', 'ترجمة مباشرة', '直播', '字幕', '实时'],
    answer: {
      en: 'The Live Intelligence Layer makes live rooms smarter for everyone in them. Real-time translated captions follow the conversation in each listener’s language, and audience questions are clustered so a host sees "34 asked about pricing" instead of a wall of chat. A whisper-assistant feeds the host private cues mid-stream, and highlights can be marked right on the stream timeline as they happen.',
      sw: 'Safu ya Akili ya Moja kwa Moja hufanya vyumba vya moja kwa moja kuwa mahiri kwa kila aliyemo. Manukuu yaliyotafsiriwa wakati halisi hufuata mazungumzo kwa lugha ya kila msikilizaji, na maswali ya hadhira hupangwa makundi ili mwenyeji aone "34 wameuliza kuhusu bei" badala ya ukuta wa mazungumzo. Msaidizi wa siri humpa mwenyeji ishara za faragha katikati ya stream, na matukio muhimu yanaweza kuwekewa alama moja kwa moja kwenye ratiba ya stream yanapotokea.',
      fr: 'La couche d’intelligence en direct rend les salles live plus intelligentes pour tous. Des sous-titres traduits en temps réel suivent la conversation dans la langue de chacun, et les questions du public sont regroupées — « 34 questions sur les tarifs » au lieu d’un mur de messages. Un assistant-murmure souffle des repères privés à l’hôte en plein direct, et les temps forts se marquent directement sur la ligne de temps du stream.',
      ar: 'طبقة الذكاء المباشر تجعل غرف البث أذكى لكل من فيها. ترجمة مصاحبة مترجمة فوريًا تتابع الحوار بلغة كل مستمع، وتُجمَّع أسئلة الجمهور ليرى المضيف "34 سألوا عن الأسعار" بدلًا من جدار من الرسائل. ومساعد همس يمد المضيف بإشارات خاصة أثناء البث، ويمكن تعليم اللحظات المميزة مباشرة على الخط الزمني للبث لحظة حدوثها.',
      zh: '直播智能层让直播间里的每个人都更从容。实时翻译字幕以每位听众的语言跟随对话；观众提问自动聚类，主播看到的是"34 人询问了价格"而不是刷屏的弹幕。耳语助手在直播中为主播提供私密提示，精彩瞬间可以直接在直播时间轴上即时标记。',
    },
    deepLink: { to: '/platform', label: 'See the platform' },
  },
  {
    id: 'a2a-registry',
    title: { en: 'Agent-to-Agent Registry', sw: 'Daftari la Mawakala kwa Mawakala', fr: 'Registre agent-à-agent', ar: 'سجل الوكيل إلى الوكيل', zh: '代理对代理注册表' },
    module: 'Developer guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['a2a', 'agent registry', 'agent-to-agent', 'verified agent', 'permission scope', 'rate limit', 'signed offer', 'registre', 'سجل الوكلاء', 'وكيل', '代理', '注册表'],
    answer: {
      en: 'The Agent-to-Agent Registry lists verified business and service agents — with badges, explicit permission scopes and rate limits — so your personal assistant can query them on your behalf. Ask for a Kilimanjaro trek quote and your assistant receives a signed, structured offer you can accept or decline. For businesses, paid agent placement in the registry is a B2B revenue stream that funds the platform without selling user attention.',
      sw: 'Daftari la Mawakala kwa Mawakala linaorodhesha mawakala wa biashara na huduma waliothibitishwa — kwa beji, ruhusa zilizobainishwa na mipaka ya mzigo — ili msaidizi wako binafsi awaulize kwa niaba yako. Omba bei ya safari ya Kilimanjaro na msaidizi wako hupokea ofa iliyosainiwa na iliyopangwa unayoweza kukubali au kukataa. Kwa biashara, kuorodheshwa kwa malipo ndani ya daftari ni njia ya mapato ya B2B inayofadhili jukwaa bila kuuza umakini wa watumiaji.',
      fr: 'Le registre agent-à-agent répertorie des agents commerciaux et de services vérifiés — avec badges, portées de permissions explicites et limites de débit — pour que votre assistant personnel les interroge en votre nom. Demandez un devis de trek au Kilimandjaro et votre assistant reçoit une offre structurée et signée, à accepter ou à décliner. Pour les entreprises, le placement payant dans le registre est un revenu B2B qui finance la plateforme sans vendre l’attention des utilisateurs.',
      ar: 'سجل الوكيل إلى الوكيل يعرض وكلاء أعمال وخدمات موثقين — بشارات ونطاقات أذونات صريحة وحدود للمعدل — ليتمكن مساعدك الشخصي من الاستفسار منهم نيابة عنك. اطلب عرض سعر لرحلة كليمنجارو فيتلقى مساعدك عرضًا منظمًا وموقّعًا يمكنك قبوله أو رفضه. وللشركات، يُعد التنسيب المدفوع في السجل مصدر إيرادات B2B يموّل المنصة دون بيع انتباه المستخدمين.',
      zh: '代理对代理注册表收录经过验证的商业和服务代理——附带徽章、明确的权限范围和速率限制——让您的个人助手可以代您向它们查询。询问乞力马扎罗徒步报价，您的助手会收到一份已签名的结构化报价，由您决定接受或拒绝。对企业而言，注册表中的付费代理位是一项 B2B 收入来源，让平台无需出售用户注意力也能获得资金。',
    },
    deepLink: { to: '/developers', label: 'Agent registry' },
  },
  {
    id: 'verifiable-credentials',
    title: { en: 'Verifiable Credentials', sw: 'Hati Zinazothibitishwa', fr: 'Attestations vérifiables', ar: 'الاعتمادات القابلة للتحقق', zh: '可验证凭证' },
    module: 'Developer guide',
    version: 'v2.16.0',
    roles: ['visitor', 'member', 'admin'],
    keywords: ['verifiable credentials', 'w3c', 'reputation', 'export reputation', 'trust score', 'credential', 'hati', 'attestation', 'اعتمادات', 'سمعة', '凭证', '声誉'],
    answer: {
      en: 'Your Kinjy reputation is yours to carry. Export expertise badges, trust scores and sales history as signed, W3C-style verifiable credentials, then present them to any other platform that can verify the signature. Identity and earned trust stop being locked inside one app and become a cross-ecosystem asset.',
      sw: 'Sifa yako ya Kinjy ni yako kubeba. Hamisha beji za utaalamu, alama za uaminifu na historia ya mauzo kama hati zilizosainiwa zinazothibitishwa za mtindo wa W3C, kisha uzionyeshe kwa jukwaa jingine lolote linaloweza kuthibitisha saini. Utambulisho na uaminifu uliopatikana huacha kufungwa ndani ya app moja na hugeuka kuwa mali ya mfumo mzima.',
      fr: 'Votre réputation Kinjy vous appartient. Exportez badges d’expertise, scores de confiance et historique de ventes sous forme d’attestations vérifiables signées, de style W3C, puis présentez-les à toute autre plateforme capable d’en vérifier la signature. L’identité et la confiance acquise cessent d’être enfermées dans une seule app et deviennent un atout inter-écosystèmes.',
      ar: 'سمعتك في كالوتا ملك لك تحملها معك. صدّر شارات الخبرة ودرجات الثقة وسجل المبيعات كاعتمادات موقّعة قابلة للتحقق بنمط W3C، ثم اعرضها على أي منصة أخرى قادرة على التحقق من التوقيع. هكذا تتحول الهوية والثقة المكتسبة من حبس داخل تطبيق واحد إلى أصل عابر للأنظمة.',
      zh: '您在 Kinjy 积累的声誉归您所有，可以随身携带。将专业徽章、信任分数和销售记录导出为 W3C 标准的已签名可验证凭证，然后向任何能够验证签名的其他平台出示。身份和辛苦赢得的信任不再被锁在单一应用内，而是成为跨生态系统的资产。',
    },
    deepLink: { to: '/developers', label: 'Credentials docs' },
  },
  {
    id: 'training-licensing',
    title: { en: 'AI-Training Licensing', sw: 'Uleseni wa Mafunzo ya AI', fr: 'Licence d’entraînement IA', ar: 'ترخيص تدريب الذكاء الاصطناعي', zh: 'AI 训练授权' },
    module: 'Developer guide',
    version: 'v2.16.0',
    roles: ['member', 'admin'],
    keywords: ['training', 'licensing', 'ai training', 'consent', 'revocation', 'compensation', 'opt-in', 'leseni', 'licence', 'ترخيص', 'تدريب', '训练授权', '同意'],
    answer: {
      en: 'Creator AI-Training Licensing puts your work under your terms. An opt-in consent panel offers granular scopes — text, video, voice — and a duration you choose, and revocation comes with a purge SLA so your content leaves the training corpus on schedule. When licensed use generates compensation, it is routed to you via the immutable ledger, traceable like every other Kinjy payment.',
      sw: 'Uleseni wa Mafunzo ya AI huweka kazi yako chini ya masharti yako. Paneli ya ridhaa ya kujiunga inatoa wigo wa kina — maandishi, video, sauti — na muda unaouchagua wewe, na kufuta ridhaa kunakuja na SLA ya kusafisha ili maudhui yako yaondoke kwenye mkusanyo wa mafunzo kwa ratiba. Fidia inapotokana na matumizi ya leseni, huelekezwa kwako kupitia ledger isiyobadilika, ikifuatiliwa kama malipo mengine yote ya Kinjy.',
      fr: 'La licence d’entraînement IA place votre œuvre sous vos conditions. Un panneau de consentement explicite offre des portées granulaires — texte, vidéo, voix — et une durée que vous choisissez, et la révocation s’accompagne d’un SLA de purge pour que votre contenu quitte le corpus d’entraînement dans les délais. Toute compensation issue de l’usage licencié vous est acheminée via le registre immuable, traçable comme tout paiement Kinjy.',
      ar: 'ترخيص تدريب الذكاء الاصطناعي يضع عملك تحت شروطك. تقدم لوحة الموافقة الاختيارية نطاقات دقيقة — نص وفيديو وصوت — ومدة تختارها بنفسك، ويأتي الإلغاء مع اتفاقية مستوى خدمة للحذف ليغادر محتواك مجموعة التدريب في موعده. وعندما ينتج عن الاستخدام المرخّص تعويض، يُوجَّه إليك عبر السجل الثابت، قابلًا للتتبع كأي دفعة أخرى في كالوتا.',
      zh: '创作者 AI 训练授权让您的作品按您的条款使用。自愿开启的同意面板提供细粒度范围——文字、视频、语音——并由您选择授权期限；撤销授权附带清除服务等级承诺，确保您的内容按时退出训练语料库。授权使用产生的报酬会通过不可篡改账本支付给您，与 Kinjy 的其他付款一样可追溯。',
    },
    deepLink: { to: '/developers', label: 'Licensing panel' },
  },
  {
    id: 'ai-quality-ops',
    title: { en: 'AI Quality & Operations', sw: 'Ubora wa AI na Uendeshaji', fr: 'Qualité IA et opérations', ar: 'جودة الذكاء الاصطناعي والعمليات', zh: 'AI 质量与运维' },
    module: 'Admin guide',
    version: 'v2.16.0',
    roles: ['admin'],
    adminOnly: true,
    keywords: ['quality gate', 'eval', 'feature flag', 'kill switch', 'observability', 'rollout', 'performance budget', 'reconciliation', 'ubora', 'qualité', 'بوابة الجودة', '质量门禁', '灰度发布'],
    answer: {
      en: 'The admin console runs platform quality as an engineering discipline. An AI evaluation harness enforces per-language quality gates that block unsafe model swaps, while feature flags ship changes through staged rollouts with kill switches. Per-provider AI observability, on-device model routing for sensitive paths, performance budgets, ledger reconciliation bots and visual-regression plus accessibility CI keep the whole system honest — with machine-readable branding for downstream tooling.',
      sw: 'Konsoli ya msimamizi huendesha ubora wa jukwaa kama fani ya uhandisi. Kifaa cha tathmini cha AI hutumia milango ya ubora kwa kila lugha inayozuia kubadilishwa kwa modeli zisizo salama, na feature flags husafirisha mabadiliko kwa rollout za hatua kwa hatua zenye kill switches. Ufuatiliaji wa AI kwa kila mtoa huduma, uelekezaji wa modeli kwenye kifaa kwa njia nyeti, bajeti za utendaji, roboti za upatanisho wa ledger na CI ya visual-regression na upatikanaji huweka mfumo mzima waaminifu — na branding inayosomeka na mashine kwa zana za chini.',
      fr: 'La console admin pilote la qualité de la plateforme comme une discipline d’ingénierie. Un harnais d’évaluation IA impose des portes qualité par langue qui bloquent les remplacements de modèles à risque, et les feature flags déploient les changements par paliers avec kill switches. Observabilité IA par fournisseur, routage de modèles sur appareil pour les chemins sensibles, budgets de performance, bots de réconciliation du registre et CI de régression visuelle et d’accessibilité gardent le système honnête — avec un branding lisible par machine pour l’outillage en aval.',
      ar: 'تدير وحدة تحكم المشرف جودة المنصة كتخصص هندسي. تفرض أداة تقييم الذكاء الاصطناعي بوابات جودة لكل لغة تحجب استبدال النماذج غير الآمن، بينما تشحن أعلام الميزات التغييرات عبر طرح تدريجي مع مفاتيح إيقاف طارئ. والمراقبة لكل مزود، وتوجيه النماذج على الجهاز للمسارات الحساسة، وميزانيات الأداء، وروبوتات مطابقة السجل، وتكامل مستمر للانحدار البصري وإمكانية الوصول — كلها تحفظ نزاهة النظام، مع هوية بصرية قابلة للقراءة آليًا للأدوات اللاحقة.',
      zh: '管理控制台将平台质量作为工程学科来运营。AI 评估框架执行按语言划分的质量门禁，阻止不安全的模型替换；功能开关通过带紧急熔断的分阶段灰度发布变更。按服务商的 AI 可观测性、敏感路径的端侧模型路由、性能预算、账本对账机器人，以及视觉回归与无障碍 CI，共同保障系统的诚实可靠——并为下游工具提供机器可读的品牌规范。',
    },
    deepLink: { to: '/admin', label: 'Open Admin Console' },
  },
  {
    id: 'commerce-copilot',
    title: { en: 'Commerce Copilot', sw: 'Msaidizi wa Biashara', fr: 'Copilote commerce', ar: 'مساعد التجارة', zh: '电商副驾' },
    module: 'Commerce guide',
    version: 'v2.16.0',
    roles: ['member', 'admin'],
    keywords: ['commerce copilot', 'listing', 'photos', 'demand forecast', 'live shopping', 'price suggestion', 'biashara', 'copilote', 'annonce', 'مساعد التجارة', 'إعلان منتج', '电商', '直播带货'],
    answer: {
      en: 'Commerce Copilot writes the boring half of selling. Snap photos of an item and it generates the listing — title, description and a suggested price — ready for you to review and publish. Demand-forecast sparklines show when your category is heating up, and a live-shopping host assistant keeps product cues and answers at hand while you stream.',
      sw: 'Msaidizi wa Biashara huandika sehemu ngumu ya kuuza. Piga picha za bidhaa nao hutengeneza orodha — jina, maelezo na bei iliyopendekezwa — tayari wewe kupitia na kuchapisha. Mistari ya utabiri wa mahitaji inaonyesha lini aina yako inapanda moto, na msaidizi wa mwenyeji wa live-shopping huweka ishara za bidhaa na majibu karibu nawe unapostream.',
      fr: 'Le Copilote commerce rédige la moitié fastidieuse de la vente. Photographiez un article et il génère l’annonce — titre, description et prix suggéré — prête à relire et à publier. Les courbes de prévision de demande montrent quand votre catégorie chauffe, et l’assistant d’animation live-shopping garde fiches produits et réponses sous la main pendant votre stream.',
      ar: 'مساعد التجارة يكتب النصف الممل من البيع. التقط صورًا للمنتج فيولّد الإعلان — العنوان والوصف وسعرًا مقترحًا — جاهزًا لمراجعتك ونشره. وتوضح منحنيات توقع الطلب متى تشتعل فئتك، بينما يُبقي مساعد مضيف التسوق المباشر معلومات المنتجات والإجابات في متناول يدك أثناء البث.',
      zh: '电商副驾替您完成卖货中繁琐的那一半。拍下商品照片，它就会生成商品信息——标题、描述和建议售价——供您审核后发布。需求预测曲线告诉您品类何时升温，直播带货助手在您开播时随时提供商品要点和应答建议。',
    },
    steps: [
      'Open Commerce and photograph the item you want to sell.',
      'Review the generated title, description and price suggestion — then publish.',
      'Go live: the host assistant feeds you product cues and answers in-stream.',
    ],
    image: '/app-feed-mock.jpg',
    imageAlt: 'Commerce copilot generating a listing from photos',
    deepLink: { to: '/commerce', label: 'Open Commerce' },
  },
  {
    id: 'ad-creative-intel',
    title: { en: 'Ad Creative Intelligence', sw: 'Akili ya Matangazo Bunifu', fr: 'Intelligence créative publicitaire', ar: 'ذكاء الإعلانات الإبداعي', zh: '广告创意智能' },
    module: 'Commerce guide',
    version: 'v2.16.0',
    roles: ['member', 'admin'],
    keywords: ['ad creative', 'brand voice', 'predicted performance', 'creative fatigue', 'ctr', 'variant', 'matangazo', 'créatif', 'publicité', 'إعلان', 'إبداعي', '广告创意', '品牌声音'],
    answer: {
      en: 'Ad Creative Intelligence turns one brief into a tested campaign. It generates ad variants trained on your brand voice, scores each with a predicted performance before you spend a cent, and fans variants out across your languages automatically. While the campaign runs, creative-fatigue alerts — "CTR decaying — rotate variant B" — tell you exactly when to refresh.',
      sw: 'Akili ya Matangazo Bunifu hubadilisha muhtasari mmoja kuwa kampeni iliyojaribiwa. Hutengeneza matangazo mbadala yaliyofunzwa kwa sauti ya chapa yako, kuyapa kila moja alama ya utendaji unaotabiriwa kabla hujatumia senti, na kusambaza matangazo kwa lugha zako kiotomatiki. Kampeni ikiendelea, arifa za uchovu wa ubunifu — "CTR inashuka — zamisha tangazo B" — hukuambia hasa lini ubadilishe.',
      fr: 'L’intelligence créative transforme un brief en campagne testée. Elle génère des variantes d’annonces entraînées sur votre voix de marque, note chacune avec une performance prédite avant la moindre dépense, et les décline automatiquement dans vos langues. Pendant la campagne, les alertes de fatigue créative — « CTR en baisse — faites tourner la variante B » — indiquent le moment exact de rafraîchir.',
      ar: 'ذكاء الإعلانات الإبداعي يحوّل موجزًا واحدًا إلى حملة مُختبَرة. يولّد نسخًا إعلانية مدرّبة على صوت علامتك، ويمنح كل نسخة درجة أداء متوقعة قبل أن تنفق فلسًا واحدًا، ويوزّع النسخ عبر لغاتك تلقائيًا. وأثناء الحملة، تنبيهات إنهاك الإبداع — "نسبة النقر تتدهور — بدّل إلى النسخة ب" — تخبرك بالضبط متى تجدد.',
      zh: '广告创意智能把一份简报变成经过测试的营销活动。它基于您的品牌声音生成多个广告变体，在您花费一分钱之前为每个变体给出预测效果分，并自动按您的语言铺开变体。投放期间，创意疲劳提醒——"点击率正在衰减——请轮换变体 B"——会精确告诉您何时该更新素材。',
    },
    deepLink: { to: '/commerce', label: 'Open Advertising' },
  },
]

export const KB_BY_ID: Record<string, KBEntry> = Object.fromEntries(KB_ENTRIES.map((e) => [e.id, e]))

/* ------------------------------------------------------------------ */
/* Language detection                                                  */
/* ------------------------------------------------------------------ */

const SW_MARKERS = ['nawezaje', 'jambo', 'habari', 'kwenye', 'mti', 'familia', 'pesa', 'fedha', 'kuthibitisha', 'vipi', 'nini', 'wapi', 'kwanini', 'tafadhali', 'asante', 'mlisho', 'duka', 'tangazo', 'akaunti', 'jamaa', 'kwa', 'na', 'ya', 'kwa', 'ninaweza', 'ni', 'sijui', 'kufuta']
const FR_MARKERS = ['comment', 'pourquoi', 'est', 'les', 'des', 'une', 'avec', 'pour', 'dans', 'sur', 'compte', 'famille', 'argent', 'publicité', 'merci', 'où', 'quoi', 'quel', 'quelle', 'faire', 'mon', 'ma', 'mes', 'je', 'vous', 'nous', 'pas', 'plus']

/** Detect the language of a free-text message; falls back to the given UI language. */
export function detectLanguage(text: string, fallback: Lang = 'en'): Lang {
  if (/[\u0600-\u06FF]/.test(text)) return 'ar'
  if (/[\u4e00-\u9fff]/.test(text)) return 'zh'
  const words = text.toLowerCase().split(/[^a-zàâäçéèêëîïôöùûü'’]+/i).filter(Boolean)
  let sw = 0
  let fr = 0
  for (const w of words) {
    if (SW_MARKERS.includes(w)) sw++
    if (FR_MARKERS.includes(w)) fr++
  }
  if (sw >= 2 && sw >= fr) return 'sw'
  if (fr >= 2) return 'fr'
  if (sw === 1 && fr === 0 && words.length <= 6) return 'sw'
  return fallback
}

/* ------------------------------------------------------------------ */
/* Intent matching                                                     */
/* ------------------------------------------------------------------ */

export interface MatchResult {
  entry: KBEntry | null
  score: number
}

/** Keyword/intent scoring against the KB. Deterministic, explainable, no fabrication. */
export function matchEntry(query: string): MatchResult {
  const q = query.toLowerCase()
  let best: KBEntry | null = null
  let bestScore = 0
  for (const entry of KB_ENTRIES) {
    let score = 0
    for (const kw of entry.keywords) {
      if (q.includes(kw)) score += kw.length >= 7 ? 3 : kw.length >= 4 ? 2 : 1
    }
    // topic-title overlap bonus
    for (const lang of Object.keys(entry.title) as Lang[]) {
      const t = entry.title[lang].toLowerCase()
      if (t.length > 3 && q.includes(t)) score += 4
    }
    if (score > bestScore) {
      bestScore = score
      best = entry
    }
  }
  if (!best || bestScore < 2) return { entry: null, score: bestScore }
  return { entry: best, score: bestScore }
}

/* ------------------------------------------------------------------ */
/* Localized chrome strings                                            */
/* ------------------------------------------------------------------ */

export const UI_STRINGS: Record<Lang, Record<string, string>> = {
  en: {
    placeholder: 'Ask about any feature…',
    listening: 'Listening… speak now',
    voiceUnsupported: 'Voice input isn’t supported by this browser — typing works everywhere.',
    tabWritten: 'Written + images',
    tabVideo: 'Video clip',
    openInApp: 'Open in app',
    fromSource: 'From',
    updated: 'updated',
    suggestionsFor: 'Popular here',
    noInfo: "I don't have verified information about that yet — I'd rather say so than guess. Try asking about feeds, family tree, memorials, marketplace, subscriptions, safety or developers.",
    adminRefusal: 'That topic is scoped to administrators, and your current access ({role}) doesn’t include it. I never reveal admin capabilities outside the admin role — if you’re an admin, switch the role demo above or open the Admin Console.',
    escalateNote: 'I’ve noted this as an escalation for the admin team.',
    videoTitle: 'Demo walkthrough',
    captionsNote: 'Captions match your language automatically',
    roleLabel: 'View as',
    visitor: 'Visitor',
    member: 'Member',
    admin: 'Admin',
    watchTab: 'AI Watch',
    send: 'Send',
    spokenReplies: 'Spoken replies',
    typing: 'Kinjy Assistant is thinking…',
    langChip: 'Replying in {lang}',
    deepLinkLabel: 'Open in app',
  },
  sw: {
    placeholder: 'Uliza kuhusu kipengele chochote…',
    listening: 'Ninasikiliza… zungumza sasa',
    voiceUnsupported: 'Ingizo la sauti halitumiki na kivinjari hiki — kuandika kunafanya kazi kote.',
    tabWritten: 'Maandishi + picha',
    tabVideo: 'Video ya demo',
    openInApp: 'Fungua kwenye app',
    fromSource: 'Kutoka',
    updated: 'imesasishwa',
    suggestionsFor: 'Maarufu hapa',
    noInfo: 'Sina taarifa zilizothibitishwa kuhusu hilo bado — nafasisi kusema hivyo kuliko kukisia. Jaribu kuuliza kuhusu mlisho, mti wa familia, makaburi, soko, usajili, usalama au wasanidi.',
    adminRefusal: 'Mada hiyo ni ya wasimamizi pekee, na ufikiaji wako wa sasa ({role}) haujajumuisha. Sisirichaji uwezo wa msimamizi nje ya jukumu hilo — kama wewe ni msimamizi, badilisha jukumu hapo juu au fungua Konsoli ya Msimamizi.',
    escalateNote: 'Nimeandika hili kama escalation kwa timu ya wasimamizi.',
    videoTitle: 'Onyesho la demo',
    captionsNote: 'Manukuu hulingana na lugha yako kiotomatiki',
    roleLabel: 'Tazama kama',
    visitor: 'Mgeni',
    member: 'Mwanachama',
    admin: 'Msimamizi',
    watchTab: 'AI Watch',
    send: 'Tuma',
    spokenReplies: 'Majibu ya sauti',
    typing: 'Kinjy Assistant inafikiria…',
    langChip: 'Najibu kwa {lang}',
    deepLinkLabel: 'Fungua kwenye app',
  },
  fr: {
    placeholder: 'Posez une question sur une fonctionnalité…',
    listening: 'Écoute en cours… parlez',
    voiceUnsupported: 'La saisie vocale n’est pas prise en charge par ce navigateur — le texte fonctionne partout.',
    tabWritten: 'Écrit + images',
    tabVideo: 'Clip vidéo',
    openInApp: 'Ouvrir dans l’app',
    fromSource: 'Source',
    updated: 'mise à jour',
    suggestionsFor: 'Populaire ici',
    noInfo: 'Je n’ai pas encore d’information vérifiée à ce sujet — je préfère le dire plutôt que deviner. Essayez les fils, l’arbre familial, les mémoriaux, la marketplace, les abonnements, la sécurité ou les développeurs.',
    adminRefusal: 'Ce sujet est réservé aux administrateurs et votre accès actuel ({role}) ne l’inclut pas. Je ne révèle jamais les capacités admin hors du rôle admin — si vous êtes admin, changez le rôle de démo ci-dessus ou ouvrez la console d’administration.',
    escalateNote: 'J’ai transmis cela comme escalade à l’équipe admin.',
    videoTitle: 'Démonstration guidée',
    captionsNote: 'Les sous-titres suivent votre langue automatiquement',
    roleLabel: 'Voir comme',
    visitor: 'Visiteur',
    member: 'Membre',
    admin: 'Admin',
    watchTab: 'Veille IA',
    send: 'Envoyer',
    spokenReplies: 'Réponses vocales',
    typing: 'Kinjy Assistant réfléchit…',
    langChip: 'Réponse en {lang}',
    deepLinkLabel: 'Ouvrir dans l’app',
  },
  ar: {
    placeholder: 'اسأل عن أي ميزة…',
    listening: 'أستمع… تحدث الآن',
    voiceUnsupported: 'الإدخال الصوتي غير مدعوم في هذا المتصفح — الكتابة تعمل في كل مكان.',
    tabWritten: 'كتابة + صور',
    tabVideo: 'مقطع فيديو',
    openInApp: 'افتح في التطبيق',
    fromSource: 'المصدر',
    updated: 'محدّث',
    suggestionsFor: 'شائع هنا',
    noInfo: 'ليس لدي معلومات موثقة عن ذلك بعد — أفضّل قول ذلك بدل التخمين. جرّب السؤال عن الخلاصات أو شجرة العائلة أو النُّصُب أو السوق أو الاشتراكات أو الأمان أو المطورين.',
    adminRefusal: 'هذا الموضوع مخصص للمشرفين، وصلاحيتك الحالية ({role}) لا تشمله. لا أكشف قدرات المشرفين خارج دور المشرف أبدًا — إذا كنت مشرفًا، بدّل الدور أعلاه أو افتح وحدة تحكم المشرف.',
    escalateNote: 'دوّنت هذا كتصعيد لفريق المشرفين.',
    videoTitle: 'جولة توضيحية',
    captionsNote: 'الترجمة المصاحبة تطابق لغتك تلقائيًا',
    roleLabel: 'اعرض بصفتي',
    visitor: 'زائر',
    member: 'عضو',
    admin: 'مشرف',
    watchTab: 'مرصد الذكاء',
    send: 'إرسال',
    spokenReplies: 'ردود صوتية',
    typing: 'مساعد كالوتا يفكر…',
    langChip: 'أجيب بـ{lang}',
    deepLinkLabel: 'افتح في التطبيق',
  },
  zh: {
    placeholder: '询问任何功能…',
    listening: '正在聆听…请说话',
    voiceUnsupported: '此浏览器不支持语音输入——文字输入在任何地方都可用。',
    tabWritten: '图文解答',
    tabVideo: '演示视频',
    openInApp: '在应用中打开',
    fromSource: '来源',
    updated: '更新于',
    suggestionsFor: '此处热门',
    noInfo: '我暂时没有关于这个问题的已验证信息——我宁可如实相告也不猜测。可以试试询问信息流、家族树、纪念园、市场、订阅、安全或开发者平台。',
    adminRefusal: '该主题仅限管理员访问，您当前的权限（{role}）不包括它。我绝不会向非管理员角色透露管理员功能——如果您是管理员，请切换上方的角色演示或打开管理控制台。',
    escalateNote: '我已将此记录为提交给管理团队的升级请求。',
    videoTitle: '演示导览',
    captionsNote: '字幕自动匹配您的语言',
    roleLabel: '以身份查看',
    visitor: '访客',
    member: '会员',
    admin: '管理员',
    watchTab: 'AI 观察',
    send: '发送',
    spokenReplies: '语音回复',
    typing: 'Kinjy 助手思考中…',
    langChip: '正在用{lang}回复',
    deepLinkLabel: '在应用中打开',
  },
}

/** Convenience formatter for "{placeholder}" templates. */
export function fmt(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '')
}

/* ------------------------------------------------------------------ */
/* Module-aware suggestion rows                                        */
/* ------------------------------------------------------------------ */

export interface ModuleContext {
  match: RegExp
  moduleName: string
  suggestionIds: string[]
}

export const MODULE_CONTEXTS: ModuleContext[] = [
  { match: /^\/family/, moduleName: 'Family Tree', suggestionIds: ['family-tree', 'heritage-ai', 'safety', 'family-year-review'] },
  { match: /^\/memorials/, moduleName: 'Digital Graveyard', suggestionIds: ['graveyard', 'remembrance-gatherings', 'family-tree', 'privacy'] },
  { match: /^\/feeds/, moduleName: 'Feeds', suggestionIds: ['feed-modes', 'algorithm-marketplace', 'translation'] },
  { match: /^\/creators/, moduleName: 'Creator Studio', suggestionIds: ['creator-studio', 'earnings', 'subscriptions'] },
  { match: /^\/commerce/, moduleName: 'Marketplace', suggestionIds: ['marketplace-margin', 'marketplace-finality', 'advertising', 'earnings', 'commerce-copilot', 'ad-creative-intel'] },
  { match: /^\/payments/, moduleName: 'Payments', suggestionIds: ['crypto-payments', 'cashout-engine', 'payout-eligibility', 'marketplace-finality'] },
  { match: /^\/pricing/, moduleName: 'Pricing', suggestionIds: ['subscriptions', 'kyc', 'earnings'] },
  { match: /^\/safety/, moduleName: 'Safety & Privacy', suggestionIds: ['safety', 'privacy', 'crisis-alerts', 'c2pa-signing', 'voice-first', 'early-warning'] },
  { match: /^\/developers/, moduleName: 'Developer Platform', suggestionIds: ['developers', 'a2a-registry', 'verifiable-credentials', 'training-licensing', 'algorithm-marketplace', 'subscriptions'] },
  { match: /^\/admin/, moduleName: 'Admin Console', suggestionIds: ['admin-aiwatch', 'admin-ledger', 'admin-fraud', 'admin-kyc-queue', 'ai-quality-ops'] },
  { match: /^\/app/, moduleName: 'Create', suggestionIds: ['creator-studio', 'feed-modes', 'onboarding-concierge', 'wellbeing', 'data-saver', 'series'] },
  { match: /^\/platform/, moduleName: 'Platform', suggestionIds: ['live-intelligence', 'translation', 'communities'] },
]

export const DEFAULT_SUGGESTIONS = ['feed-modes', 'family-tree', 'crypto-payments']

export function contextForPath(pathname: string): ModuleContext | null {
  return MODULE_CONTEXTS.find((c) => c.match.test(pathname)) ?? null
}
