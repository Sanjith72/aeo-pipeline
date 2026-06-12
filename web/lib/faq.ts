// FAQ content lives here (not in the component) so the visible accordion and the
// FAQPage JSON-LD in app/layout.tsx are always the same words — an AEO product
// should practice what it sells.

export type FaqItem = { q: string; a: string };

export const FAQ_ITEMS: FaqItem[] = [
  {
    q: "What is answer engine optimization (AEO)?",
    a: "AEO is the practice of making your business the answer AI assistants give. When someone asks ChatGPT, Perplexity, or Google AI for a recommendation, those engines pick a handful of businesses to cite. AEO Studio analyzes how those engines see your website and gives you a concrete plan to become the business they recommend.",
  },
  {
    q: "How is AEO different from SEO?",
    a: "SEO competes for a position in a list of links. AEO competes for the single answer an AI gives — there's no page two. The fundamentals overlap (clear structure, real expertise, machine-readable facts), but AEO prioritizes the things answer engines actually cite: direct answers to real customer questions, consistent entity information, and structured data. Your AEO plan strengthens your SEO as a side effect, not the other way around.",
  },
  {
    q: "Do I need a technical background to use this?",
    a: "No. You answer five plain-English questions about your business. The plan comes back in plain English too — what to do, in what order, and how much work each step is. For the technical parts, the launch kit includes ready-made files you can hand to whoever builds your website, whether that's you, an AI tool, a freelancer, or your developer.",
  },
  {
    q: "How long does it take?",
    a: "The five questions take about a minute. A plan built from your answers arrives in seconds; a quick look at your live site takes about a minute; the full page-by-page check-up runs five to fifteen minutes. The plan itself is organized as a 30-day checklist you work through at your own pace.",
  },
  {
    q: "What exactly do I get?",
    a: "Three things. First, a website blueprint: the exact pages your site needs, ranked by how much they matter for AI answers. Second, a prioritized action plan with the effort level of each step. Third, a launch kit — a folder of ready-to-use files (page outlines, content briefs, structured-data snippets, and a 30-day checklist) adapted to whoever is building your site.",
  },
  {
    q: "What happens to my data?",
    a: "Your business details are used to build your plan and for nothing else. They are never sold, shared, or used to train models. Your reports belong to you — download everything as files and use them anywhere.",
  },
  {
    q: "I don't have a website yet. Does this still work?",
    a: "Yes — that's one of the best times to use it. Skip the site check-up and AEO Studio plans your ideal website from your answers alone, so you build it right the first time instead of retrofitting AI visibility later.",
  },
];
