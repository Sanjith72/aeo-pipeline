// Curated choices for the guided form — dropdowns instead of free text wherever the
// answer space is known. Every list stays editable: the comboboxes accept custom values.

export const INDUSTRIES = [
  "Accounting & Tax",
  "Automotive",
  "Construction & Trades",
  "Consulting",
  "Cybersecurity",
  "E-commerce & Retail",
  "Education & Training",
  "Finance & Insurance",
  "Fitness & Wellness",
  "Healthcare & Medical",
  "Home Services",
  "Hospitality & Travel",
  "Legal Services",
  "Manufacturing",
  "Marketing & Advertising",
  "Nonprofit",
  "Real Estate",
  "Restaurants & Food",
  "SaaS & Software",
  "Other",
] as const;

export const LOCATIONS = [
  "United States",
  "New York, US",
  "Los Angeles, US",
  "Chicago, US",
  "Houston, US",
  "Boston, US",
  "San Francisco, US",
  "Seattle, US",
  "Austin, US",
  "Miami, US",
  "United Kingdom",
  "London, UK",
  "Canada",
  "Toronto, CA",
  "Vancouver, CA",
  "Australia",
  "Sydney, AU",
  "Germany",
  "Berlin, DE",
  "India",
  "Bengaluru, IN",
  "Chennai, IN",
  "Mumbai, IN",
  "Singapore",
  "Dubai, UAE",
  "Online only / worldwide",
];

export const BUSINESS_SIZES = [
  { value: "solo", label: "Just me" },
  { value: "2-10", label: "2–10 people" },
  { value: "11-50", label: "11–50 people" },
  { value: "51-200", label: "51–200 people" },
  { value: "200+", label: "200+ people" },
];

// Outcome language only — what the owner wants, not how the pipeline works.
export const GOAL_OPTIONS = [
  {
    label: "Show up in AI answers",
    hint: "Be the business ChatGPT and Google AI recommend",
  },
  { label: "Win more customers", hint: "Turn searches into enquiries and sales" },
  { label: "Beat my competitors", hint: "See what they do well and do it better" },
  { label: "Grow local business", hint: "Get found by people searching nearby" },
  { label: "Build my brand's authority", hint: "Become the trusted name in your field" },
  { label: "Sell more online", hint: "Help shoppers find and choose your products" },
];

// Who's building the website — decides which flavor of launch kit the API packs.
export const BUILDER_MODES = [
  {
    value: "diy",
    label: "Me, with a website builder",
    hint: "Paste-ready pages for Wix, Squarespace, WordPress…",
  },
  { value: "ai", label: "AI tools", hint: "Copy-paste prompts for ChatGPT or your builder's AI" },
  {
    value: "hire",
    label: "I'll hire someone",
    hint: "A ready-to-post job brief, plus how to check the work",
  },
  { value: "dev", label: "My developer or agency", hint: "Technical files they can build from directly" },
] as const;

export type BuilderMode = (typeof BUILDER_MODES)[number]["value"];

// Friendly names for the analysis labels the API returns.
export const EFFORT_LABEL: Record<string, string> = {
  low: "Quick win",
  medium: "Medium lift",
  high: "Big project",
};

// Keys mirror aeo.intelligence.scenario.Scenario values.
export const SCENARIO_LABEL: Record<string, string> = {
  no_website: "New website plan",
  single_page: "One-page site",
  small_site: "Small site",
  growing_site: "Growing site",
  mature_site: "Established site",
};

// Friendly names for the consultant-speak deliverable titles the API returns
// (aeo.intelligence.config._DEFAULT_DELIVERABLES).
export const DELIVERABLE_LABEL: Record<string, string> = {
  "AEO Website Blueprint": "Your complete website plan",
  "Restructuring Roadmap": "Your site makeover roadmap",
  "Gap Analysis & Build Plan": "What's missing & what to build",
  "Gap Analysis & Authority Plan": "What's missing & how to lead",
  "Consolidation & Authority Plan": "Tidy up & take the lead",
};

// What each page-intent token means for the owner (blueprint table).
export const INTENT_LABEL: Record<string, string> = {
  informational: "answers questions",
  commercial: "helps people choose you",
  transactional: "turns visitors into customers",
  navigational: "helps people find you",
};

export function humanizeToken(value: string): string {
  return value.replace(/[_-]+/g, " ").trim();
}
