// FAQ content lives here (not in the component) so the visible accordion and the
// FAQPage JSON-LD in app/page.tsx are always the same words — an AEO product
// should practice what it sells.

export type FaqItem = { q: string; a: string };

export const FAQ_ITEMS: FaqItem[] = [
  {
    q: "What is answer engine optimization (AEO)?",
    a: "AEO is the practice of making your business the answer AI assistants give. When someone asks ChatGPT, Perplexity, or Google AI for a recommendation, those engines pick a handful of businesses to cite. AEO Studio analyzes how those engines see your website and gives you a concrete plan to become the business they recommend.",
  },
  {
    q: "How is AEO different from SEO?",
    a: "SEO helps you rank in a list of ten blue links. AEO helps you be the single answer an AI assistant gives. The signals overlap, but answer engines care much more about clear, direct answers, credibility, and structured facts — and there's no page two to fall back on.",
  },
  {
    q: "Do I need a technical background to use this?",
    a: "No. You answer five plain-English questions and we handle the analysis. Your plan reads like a to-do list, not a technical audit — and the ready-made files that come with it can be handed straight to whoever looks after your website.",
  },
  {
    q: "How long does it take?",
    a: "The analysis runs in minutes and your plan arrives the same day. How fast you see results depends on how quickly the changes go live — most items on the checklist are small edits, not rebuilds.",
  },
  {
    q: "What exactly do I get?",
    a: "A visibility score showing how answer engines read your business today, a prioritized checklist in plain English, and ready-made files — page outlines, structured data, suggested copy — your web person can use immediately.",
  },
  {
    q: "What happens to my data?",
    a: "Your business details are used only to build your plan. They're never shared, sold, or sent anywhere else, and you can ask us to delete them at any time.",
  },
  {
    q: "I don't have a website yet. Does this still work?",
    a: "Yes. Skip the website step and we'll build your plan from the basics — what you do, where you are, and who you're up against. The plan will include what your first pages need so you start visible from day one.",
  },
];
