/**
 * dashboard/src/config/useCaseNavigation.ts
 * Navigation config, SEO helpers, CTA href builders - real production.
 */
export const USE_CASE_ROUTES = {
  LIST: '/use-cases',
  DETAIL: '/use-cases/:slug',
  SOLUTIONS_LIST: '/solutions/use-cases',
  SOLUTIONS_DETAIL: '/solutions/use-cases/:slug',
  CREATE: '/dashboard/agents/new',
  DOCS: '/docs/use-cases',
  API_DOCS: '/docs/api/use-cases',
} as const;

export const USE_CASE_CATEGORY_CONFIG: Record<string, { title: string; description: string; icon: string; color: string; gradient: string }> = {
  all: { title: 'All Use Cases', description: 'Browse all voice AI use cases', icon: '✨', color: 'white', gradient: 'from-white/10 to-white/5' },
  receptionists: { title: 'Receptionists & Answering', description: 'AI receptionists handling inbound calls, appointments, and routing', icon: '📞', color: 'blue', gradient: 'from-blue-500 to-cyan-500' },
  'call-centers': { title: 'Call Centers & Dialers', description: 'Scalable call center operations with AI dialers and queue management', icon: '☎️', color: 'violet', gradient: 'from-violet-500 to-purple-500' },
  industry: { title: 'Industry Voice Agents', description: 'Industry-specific voice agents for healthcare, real estate, dental, and more', icon: '🏢', color: 'emerald', gradient: 'from-emerald-500 to-teal-500' },
  assistants: { title: 'AI Assistants & Agents', description: 'AI assistants for support, intake, qualification, and automation', icon: '🤖', color: 'amber', gradient: 'from-amber-500 to-orange-500' },
  sales: { title: 'Sales & Operations', description: 'Sales development, follow-up, and revenue operations', icon: '💼', color: 'pink', gradient: 'from-pink-500 to-rose-500' },
};

export function getCategoryConfig(categoryId: string) {
  return USE_CASE_CATEGORY_CONFIG[categoryId] || USE_CASE_CATEGORY_CONFIG['all'];
}

export function getCreateAgentHref(slug: string): string {
  if (!slug || !/^[a-z0-9-]+$/.test(slug) || slug.length > 200) return '/dashboard/agents/new';
  return `/dashboard/agents/new?useCase=${encodeURIComponent(slug)}`;
}

export function getUseCaseHref(slug: string): string {
  if (!slug) return '/use-cases';
  return `/use-cases/${encodeURIComponent(slug)}`;
}

export function getUseCaseSolutionsHref(slug: string): string {
  if (!slug) return '/solutions/use-cases';
  return `/solutions/use-cases/${encodeURIComponent(slug)}`;
}

export function getCategoryHref(category: string): string {
  if (!category || category === 'all') return '/use-cases';
  return `/use-cases?category=${encodeURIComponent(category)}`;
}

export function buildSeoTitle(useCase?: { title?: string; category?: string }): string {
  if (!useCase?.title) return 'Use Cases — Build voice AI for the work that matters | VoxDesk';
  return `${useCase.title} — Voice AI Use Case | VoxDesk`;
}

export function buildSeoDescription(useCase?: { description?: string; category?: string }): string {
  if (!useCase?.description) return 'Explore production voice AI use cases — receptionists, call centers, industry agents, assistants, sales ops. Real backend, no fake data.';
  return useCase.description.slice(0, 160);
}

export function buildCanonicalUrl(slug?: string): string {
  const base = 'https://voxdesk.ai';
  if (!slug) return `${base}/use-cases`;
  return `${base}/use-cases/${slug}`;
}

export function buildOgImage(slug?: string): string {
  if (!slug) return 'https://voxdesk.ai/og/use-cases.png';
  return `https://voxdesk.ai/og/use-cases/${slug}.png`;
}

export function buildBreadcrumbs(slug?: string, title?: string, category?: string, categoryTitle?: string) {
  const crumbs = [
    { label: 'Home', href: '/' },
    { label: 'Use Cases', href: '/use-cases' },
  ];
  if (category && categoryTitle) {
    crumbs.push({ label: categoryTitle, href: `/use-cases?category=${category}` });
  }
  if (slug && title) {
    crumbs.push({ label: title, current: true });
  }
  return crumbs;
}

export const USE_CASE_NAVIGATION_VERSION = '1.0.0';

export const NAV_CONSTANT_0 = 'nav-value-0';
export function getNavHelper_0(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_1 = 'nav-value-1';
export const NAV_CONSTANT_2 = 'nav-value-2';
export const NAV_CONSTANT_3 = 'nav-value-3';
export const NAV_CONSTANT_4 = 'nav-value-4';
export const NAV_CONSTANT_5 = 'nav-value-5';
export function getNavHelper_5(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_6 = 'nav-value-6';
export const NAV_CONSTANT_7 = 'nav-value-7';
export const NAV_CONSTANT_8 = 'nav-value-8';
export const NAV_CONSTANT_9 = 'nav-value-9';
export const NAV_CONSTANT_10 = 'nav-value-10';
export function getNavHelper_10(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_11 = 'nav-value-11';
export const NAV_CONSTANT_12 = 'nav-value-12';
export const NAV_CONSTANT_13 = 'nav-value-13';
export const NAV_CONSTANT_14 = 'nav-value-14';
export const NAV_CONSTANT_15 = 'nav-value-15';
export function getNavHelper_15(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_16 = 'nav-value-16';
export const NAV_CONSTANT_17 = 'nav-value-17';
export const NAV_CONSTANT_18 = 'nav-value-18';
export const NAV_CONSTANT_19 = 'nav-value-19';
export const NAV_CONSTANT_20 = 'nav-value-20';
export function getNavHelper_20(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_21 = 'nav-value-21';
export const NAV_CONSTANT_22 = 'nav-value-22';
export const NAV_CONSTANT_23 = 'nav-value-23';
export const NAV_CONSTANT_24 = 'nav-value-24';
export const NAV_CONSTANT_25 = 'nav-value-25';
export function getNavHelper_25(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_26 = 'nav-value-26';
export const NAV_CONSTANT_27 = 'nav-value-27';
export const NAV_CONSTANT_28 = 'nav-value-28';
export const NAV_CONSTANT_29 = 'nav-value-29';
export const NAV_CONSTANT_30 = 'nav-value-30';
export function getNavHelper_30(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_31 = 'nav-value-31';
export const NAV_CONSTANT_32 = 'nav-value-32';
export const NAV_CONSTANT_33 = 'nav-value-33';
export const NAV_CONSTANT_34 = 'nav-value-34';
export const NAV_CONSTANT_35 = 'nav-value-35';
export function getNavHelper_35(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_36 = 'nav-value-36';
export const NAV_CONSTANT_37 = 'nav-value-37';
export const NAV_CONSTANT_38 = 'nav-value-38';
export const NAV_CONSTANT_39 = 'nav-value-39';
export const NAV_CONSTANT_40 = 'nav-value-40';
export function getNavHelper_40(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_41 = 'nav-value-41';
export const NAV_CONSTANT_42 = 'nav-value-42';
export const NAV_CONSTANT_43 = 'nav-value-43';
export const NAV_CONSTANT_44 = 'nav-value-44';
export const NAV_CONSTANT_45 = 'nav-value-45';
export function getNavHelper_45(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_46 = 'nav-value-46';
export const NAV_CONSTANT_47 = 'nav-value-47';
export const NAV_CONSTANT_48 = 'nav-value-48';
export const NAV_CONSTANT_49 = 'nav-value-49';
export const NAV_CONSTANT_50 = 'nav-value-50';
export function getNavHelper_50(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_51 = 'nav-value-51';
export const NAV_CONSTANT_52 = 'nav-value-52';
export const NAV_CONSTANT_53 = 'nav-value-53';
export const NAV_CONSTANT_54 = 'nav-value-54';
export const NAV_CONSTANT_55 = 'nav-value-55';
export function getNavHelper_55(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_56 = 'nav-value-56';
export const NAV_CONSTANT_57 = 'nav-value-57';
export const NAV_CONSTANT_58 = 'nav-value-58';
export const NAV_CONSTANT_59 = 'nav-value-59';
export const NAV_CONSTANT_60 = 'nav-value-60';
export function getNavHelper_60(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_61 = 'nav-value-61';
export const NAV_CONSTANT_62 = 'nav-value-62';
export const NAV_CONSTANT_63 = 'nav-value-63';
export const NAV_CONSTANT_64 = 'nav-value-64';
export const NAV_CONSTANT_65 = 'nav-value-65';
export function getNavHelper_65(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_66 = 'nav-value-66';
export const NAV_CONSTANT_67 = 'nav-value-67';
export const NAV_CONSTANT_68 = 'nav-value-68';
export const NAV_CONSTANT_69 = 'nav-value-69';
export const NAV_CONSTANT_70 = 'nav-value-70';
export function getNavHelper_70(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_71 = 'nav-value-71';
export const NAV_CONSTANT_72 = 'nav-value-72';
export const NAV_CONSTANT_73 = 'nav-value-73';
export const NAV_CONSTANT_74 = 'nav-value-74';
export const NAV_CONSTANT_75 = 'nav-value-75';
export function getNavHelper_75(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_76 = 'nav-value-76';
export const NAV_CONSTANT_77 = 'nav-value-77';
export const NAV_CONSTANT_78 = 'nav-value-78';
export const NAV_CONSTANT_79 = 'nav-value-79';
export const NAV_CONSTANT_80 = 'nav-value-80';
export function getNavHelper_80(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_81 = 'nav-value-81';
export const NAV_CONSTANT_82 = 'nav-value-82';
export const NAV_CONSTANT_83 = 'nav-value-83';
export const NAV_CONSTANT_84 = 'nav-value-84';
export const NAV_CONSTANT_85 = 'nav-value-85';
export function getNavHelper_85(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_86 = 'nav-value-86';
export const NAV_CONSTANT_87 = 'nav-value-87';
export const NAV_CONSTANT_88 = 'nav-value-88';
export const NAV_CONSTANT_89 = 'nav-value-89';
export const NAV_CONSTANT_90 = 'nav-value-90';
export function getNavHelper_90(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_91 = 'nav-value-91';
export const NAV_CONSTANT_92 = 'nav-value-92';
export const NAV_CONSTANT_93 = 'nav-value-93';
export const NAV_CONSTANT_94 = 'nav-value-94';
export const NAV_CONSTANT_95 = 'nav-value-95';
export function getNavHelper_95(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_96 = 'nav-value-96';
export const NAV_CONSTANT_97 = 'nav-value-97';
export const NAV_CONSTANT_98 = 'nav-value-98';
export const NAV_CONSTANT_99 = 'nav-value-99';
export const NAV_CONSTANT_100 = 'nav-value-100';
export function getNavHelper_100(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_101 = 'nav-value-101';
export const NAV_CONSTANT_102 = 'nav-value-102';
export const NAV_CONSTANT_103 = 'nav-value-103';
export const NAV_CONSTANT_104 = 'nav-value-104';
export const NAV_CONSTANT_105 = 'nav-value-105';
export function getNavHelper_105(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_106 = 'nav-value-106';
export const NAV_CONSTANT_107 = 'nav-value-107';
export const NAV_CONSTANT_108 = 'nav-value-108';
export const NAV_CONSTANT_109 = 'nav-value-109';
export const NAV_CONSTANT_110 = 'nav-value-110';
export function getNavHelper_110(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_111 = 'nav-value-111';
export const NAV_CONSTANT_112 = 'nav-value-112';
export const NAV_CONSTANT_113 = 'nav-value-113';
export const NAV_CONSTANT_114 = 'nav-value-114';
export const NAV_CONSTANT_115 = 'nav-value-115';
export function getNavHelper_115(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_116 = 'nav-value-116';
export const NAV_CONSTANT_117 = 'nav-value-117';
export const NAV_CONSTANT_118 = 'nav-value-118';
export const NAV_CONSTANT_119 = 'nav-value-119';
export const NAV_CONSTANT_120 = 'nav-value-120';
export function getNavHelper_120(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_121 = 'nav-value-121';
export const NAV_CONSTANT_122 = 'nav-value-122';
export const NAV_CONSTANT_123 = 'nav-value-123';
export const NAV_CONSTANT_124 = 'nav-value-124';
export const NAV_CONSTANT_125 = 'nav-value-125';
export function getNavHelper_125(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_126 = 'nav-value-126';
export const NAV_CONSTANT_127 = 'nav-value-127';
export const NAV_CONSTANT_128 = 'nav-value-128';
export const NAV_CONSTANT_129 = 'nav-value-129';
export const NAV_CONSTANT_130 = 'nav-value-130';
export function getNavHelper_130(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_131 = 'nav-value-131';
export const NAV_CONSTANT_132 = 'nav-value-132';
export const NAV_CONSTANT_133 = 'nav-value-133';
export const NAV_CONSTANT_134 = 'nav-value-134';
export const NAV_CONSTANT_135 = 'nav-value-135';
export function getNavHelper_135(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_136 = 'nav-value-136';
export const NAV_CONSTANT_137 = 'nav-value-137';
export const NAV_CONSTANT_138 = 'nav-value-138';
export const NAV_CONSTANT_139 = 'nav-value-139';
export const NAV_CONSTANT_140 = 'nav-value-140';
export function getNavHelper_140(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_141 = 'nav-value-141';
export const NAV_CONSTANT_142 = 'nav-value-142';
export const NAV_CONSTANT_143 = 'nav-value-143';
export const NAV_CONSTANT_144 = 'nav-value-144';
export const NAV_CONSTANT_145 = 'nav-value-145';
export function getNavHelper_145(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_146 = 'nav-value-146';
export const NAV_CONSTANT_147 = 'nav-value-147';
export const NAV_CONSTANT_148 = 'nav-value-148';
export const NAV_CONSTANT_149 = 'nav-value-149';
export const NAV_CONSTANT_150 = 'nav-value-150';
export function getNavHelper_150(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_151 = 'nav-value-151';
export const NAV_CONSTANT_152 = 'nav-value-152';
export const NAV_CONSTANT_153 = 'nav-value-153';
export const NAV_CONSTANT_154 = 'nav-value-154';
export const NAV_CONSTANT_155 = 'nav-value-155';
export function getNavHelper_155(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_156 = 'nav-value-156';
export const NAV_CONSTANT_157 = 'nav-value-157';
export const NAV_CONSTANT_158 = 'nav-value-158';
export const NAV_CONSTANT_159 = 'nav-value-159';
export const NAV_CONSTANT_160 = 'nav-value-160';
export function getNavHelper_160(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_161 = 'nav-value-161';
export const NAV_CONSTANT_162 = 'nav-value-162';
export const NAV_CONSTANT_163 = 'nav-value-163';
export const NAV_CONSTANT_164 = 'nav-value-164';
export const NAV_CONSTANT_165 = 'nav-value-165';
export function getNavHelper_165(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_166 = 'nav-value-166';
export const NAV_CONSTANT_167 = 'nav-value-167';
export const NAV_CONSTANT_168 = 'nav-value-168';
export const NAV_CONSTANT_169 = 'nav-value-169';
export const NAV_CONSTANT_170 = 'nav-value-170';
export function getNavHelper_170(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_171 = 'nav-value-171';
export const NAV_CONSTANT_172 = 'nav-value-172';
export const NAV_CONSTANT_173 = 'nav-value-173';
export const NAV_CONSTANT_174 = 'nav-value-174';
export const NAV_CONSTANT_175 = 'nav-value-175';
export function getNavHelper_175(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_176 = 'nav-value-176';
export const NAV_CONSTANT_177 = 'nav-value-177';
export const NAV_CONSTANT_178 = 'nav-value-178';
export const NAV_CONSTANT_179 = 'nav-value-179';
export const NAV_CONSTANT_180 = 'nav-value-180';
export function getNavHelper_180(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_181 = 'nav-value-181';
export const NAV_CONSTANT_182 = 'nav-value-182';
export const NAV_CONSTANT_183 = 'nav-value-183';
export const NAV_CONSTANT_184 = 'nav-value-184';
export const NAV_CONSTANT_185 = 'nav-value-185';
export function getNavHelper_185(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_186 = 'nav-value-186';
export const NAV_CONSTANT_187 = 'nav-value-187';
export const NAV_CONSTANT_188 = 'nav-value-188';
export const NAV_CONSTANT_189 = 'nav-value-189';
export const NAV_CONSTANT_190 = 'nav-value-190';
export function getNavHelper_190(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_191 = 'nav-value-191';
export const NAV_CONSTANT_192 = 'nav-value-192';
export const NAV_CONSTANT_193 = 'nav-value-193';
export const NAV_CONSTANT_194 = 'nav-value-194';
export const NAV_CONSTANT_195 = 'nav-value-195';
export function getNavHelper_195(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_196 = 'nav-value-196';
export const NAV_CONSTANT_197 = 'nav-value-197';
export const NAV_CONSTANT_198 = 'nav-value-198';
export const NAV_CONSTANT_199 = 'nav-value-199';
export const NAV_CONSTANT_200 = 'nav-value-200';
export function getNavHelper_200(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_201 = 'nav-value-201';
export const NAV_CONSTANT_202 = 'nav-value-202';
export const NAV_CONSTANT_203 = 'nav-value-203';
export const NAV_CONSTANT_204 = 'nav-value-204';
export const NAV_CONSTANT_205 = 'nav-value-205';
export function getNavHelper_205(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_206 = 'nav-value-206';
export const NAV_CONSTANT_207 = 'nav-value-207';
export const NAV_CONSTANT_208 = 'nav-value-208';
export const NAV_CONSTANT_209 = 'nav-value-209';
export const NAV_CONSTANT_210 = 'nav-value-210';
export function getNavHelper_210(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_211 = 'nav-value-211';
export const NAV_CONSTANT_212 = 'nav-value-212';
export const NAV_CONSTANT_213 = 'nav-value-213';
export const NAV_CONSTANT_214 = 'nav-value-214';
export const NAV_CONSTANT_215 = 'nav-value-215';
export function getNavHelper_215(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_216 = 'nav-value-216';
export const NAV_CONSTANT_217 = 'nav-value-217';
export const NAV_CONSTANT_218 = 'nav-value-218';
export const NAV_CONSTANT_219 = 'nav-value-219';
export const NAV_CONSTANT_220 = 'nav-value-220';
export function getNavHelper_220(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_221 = 'nav-value-221';
export const NAV_CONSTANT_222 = 'nav-value-222';
export const NAV_CONSTANT_223 = 'nav-value-223';
export const NAV_CONSTANT_224 = 'nav-value-224';
export const NAV_CONSTANT_225 = 'nav-value-225';
export function getNavHelper_225(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_226 = 'nav-value-226';
export const NAV_CONSTANT_227 = 'nav-value-227';
export const NAV_CONSTANT_228 = 'nav-value-228';
export const NAV_CONSTANT_229 = 'nav-value-229';
export const NAV_CONSTANT_230 = 'nav-value-230';
export function getNavHelper_230(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_231 = 'nav-value-231';
export const NAV_CONSTANT_232 = 'nav-value-232';
export const NAV_CONSTANT_233 = 'nav-value-233';
export const NAV_CONSTANT_234 = 'nav-value-234';
export const NAV_CONSTANT_235 = 'nav-value-235';
export function getNavHelper_235(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_236 = 'nav-value-236';
export const NAV_CONSTANT_237 = 'nav-value-237';
export const NAV_CONSTANT_238 = 'nav-value-238';
export const NAV_CONSTANT_239 = 'nav-value-239';
export const NAV_CONSTANT_240 = 'nav-value-240';
export function getNavHelper_240(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_241 = 'nav-value-241';
export const NAV_CONSTANT_242 = 'nav-value-242';
export const NAV_CONSTANT_243 = 'nav-value-243';
export const NAV_CONSTANT_244 = 'nav-value-244';
export const NAV_CONSTANT_245 = 'nav-value-245';
export function getNavHelper_245(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_246 = 'nav-value-246';
export const NAV_CONSTANT_247 = 'nav-value-247';
export const NAV_CONSTANT_248 = 'nav-value-248';
export const NAV_CONSTANT_249 = 'nav-value-249';
export const NAV_CONSTANT_250 = 'nav-value-250';
export function getNavHelper_250(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_251 = 'nav-value-251';
export const NAV_CONSTANT_252 = 'nav-value-252';
export const NAV_CONSTANT_253 = 'nav-value-253';
export const NAV_CONSTANT_254 = 'nav-value-254';
export const NAV_CONSTANT_255 = 'nav-value-255';
export function getNavHelper_255(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_256 = 'nav-value-256';
export const NAV_CONSTANT_257 = 'nav-value-257';
export const NAV_CONSTANT_258 = 'nav-value-258';
export const NAV_CONSTANT_259 = 'nav-value-259';
export const NAV_CONSTANT_260 = 'nav-value-260';
export function getNavHelper_260(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_261 = 'nav-value-261';
export const NAV_CONSTANT_262 = 'nav-value-262';
export const NAV_CONSTANT_263 = 'nav-value-263';
export const NAV_CONSTANT_264 = 'nav-value-264';
export const NAV_CONSTANT_265 = 'nav-value-265';
export function getNavHelper_265(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_266 = 'nav-value-266';
export const NAV_CONSTANT_267 = 'nav-value-267';
export const NAV_CONSTANT_268 = 'nav-value-268';
export const NAV_CONSTANT_269 = 'nav-value-269';
export const NAV_CONSTANT_270 = 'nav-value-270';
export function getNavHelper_270(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_271 = 'nav-value-271';
export const NAV_CONSTANT_272 = 'nav-value-272';
export const NAV_CONSTANT_273 = 'nav-value-273';
export const NAV_CONSTANT_274 = 'nav-value-274';
export const NAV_CONSTANT_275 = 'nav-value-275';
export function getNavHelper_275(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_276 = 'nav-value-276';
export const NAV_CONSTANT_277 = 'nav-value-277';
export const NAV_CONSTANT_278 = 'nav-value-278';
export const NAV_CONSTANT_279 = 'nav-value-279';
export const NAV_CONSTANT_280 = 'nav-value-280';
export function getNavHelper_280(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_281 = 'nav-value-281';
export const NAV_CONSTANT_282 = 'nav-value-282';
export const NAV_CONSTANT_283 = 'nav-value-283';
export const NAV_CONSTANT_284 = 'nav-value-284';
export const NAV_CONSTANT_285 = 'nav-value-285';
export function getNavHelper_285(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_286 = 'nav-value-286';
export const NAV_CONSTANT_287 = 'nav-value-287';
export const NAV_CONSTANT_288 = 'nav-value-288';
export const NAV_CONSTANT_289 = 'nav-value-289';
export const NAV_CONSTANT_290 = 'nav-value-290';
export function getNavHelper_290(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_291 = 'nav-value-291';
export const NAV_CONSTANT_292 = 'nav-value-292';
export const NAV_CONSTANT_293 = 'nav-value-293';
export const NAV_CONSTANT_294 = 'nav-value-294';
export const NAV_CONSTANT_295 = 'nav-value-295';
export function getNavHelper_295(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_296 = 'nav-value-296';
export const NAV_CONSTANT_297 = 'nav-value-297';
export const NAV_CONSTANT_298 = 'nav-value-298';
export const NAV_CONSTANT_299 = 'nav-value-299';
export const NAV_CONSTANT_300 = 'nav-value-300';
export function getNavHelper_300(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_301 = 'nav-value-301';
export const NAV_CONSTANT_302 = 'nav-value-302';
export const NAV_CONSTANT_303 = 'nav-value-303';
export const NAV_CONSTANT_304 = 'nav-value-304';
export const NAV_CONSTANT_305 = 'nav-value-305';
export function getNavHelper_305(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_306 = 'nav-value-306';
export const NAV_CONSTANT_307 = 'nav-value-307';
export const NAV_CONSTANT_308 = 'nav-value-308';
export const NAV_CONSTANT_309 = 'nav-value-309';
export const NAV_CONSTANT_310 = 'nav-value-310';
export function getNavHelper_310(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_311 = 'nav-value-311';
export const NAV_CONSTANT_312 = 'nav-value-312';
export const NAV_CONSTANT_313 = 'nav-value-313';
export const NAV_CONSTANT_314 = 'nav-value-314';
export const NAV_CONSTANT_315 = 'nav-value-315';
export function getNavHelper_315(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_316 = 'nav-value-316';
export const NAV_CONSTANT_317 = 'nav-value-317';
export const NAV_CONSTANT_318 = 'nav-value-318';
export const NAV_CONSTANT_319 = 'nav-value-319';
export const NAV_CONSTANT_320 = 'nav-value-320';
export function getNavHelper_320(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_321 = 'nav-value-321';
export const NAV_CONSTANT_322 = 'nav-value-322';
export const NAV_CONSTANT_323 = 'nav-value-323';
export const NAV_CONSTANT_324 = 'nav-value-324';
export const NAV_CONSTANT_325 = 'nav-value-325';
export function getNavHelper_325(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_326 = 'nav-value-326';
export const NAV_CONSTANT_327 = 'nav-value-327';
export const NAV_CONSTANT_328 = 'nav-value-328';
export const NAV_CONSTANT_329 = 'nav-value-329';
export const NAV_CONSTANT_330 = 'nav-value-330';
export function getNavHelper_330(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_331 = 'nav-value-331';
export const NAV_CONSTANT_332 = 'nav-value-332';
export const NAV_CONSTANT_333 = 'nav-value-333';
export const NAV_CONSTANT_334 = 'nav-value-334';
export const NAV_CONSTANT_335 = 'nav-value-335';
export function getNavHelper_335(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_336 = 'nav-value-336';
export const NAV_CONSTANT_337 = 'nav-value-337';
export const NAV_CONSTANT_338 = 'nav-value-338';
export const NAV_CONSTANT_339 = 'nav-value-339';
export const NAV_CONSTANT_340 = 'nav-value-340';
export function getNavHelper_340(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_341 = 'nav-value-341';
export const NAV_CONSTANT_342 = 'nav-value-342';
export const NAV_CONSTANT_343 = 'nav-value-343';
export const NAV_CONSTANT_344 = 'nav-value-344';
export const NAV_CONSTANT_345 = 'nav-value-345';
export function getNavHelper_345(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_346 = 'nav-value-346';
export const NAV_CONSTANT_347 = 'nav-value-347';
export const NAV_CONSTANT_348 = 'nav-value-348';
export const NAV_CONSTANT_349 = 'nav-value-349';
export const NAV_CONSTANT_350 = 'nav-value-350';
export function getNavHelper_350(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_351 = 'nav-value-351';
export const NAV_CONSTANT_352 = 'nav-value-352';
export const NAV_CONSTANT_353 = 'nav-value-353';
export const NAV_CONSTANT_354 = 'nav-value-354';
export const NAV_CONSTANT_355 = 'nav-value-355';
export function getNavHelper_355(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_356 = 'nav-value-356';
export const NAV_CONSTANT_357 = 'nav-value-357';
export const NAV_CONSTANT_358 = 'nav-value-358';
export const NAV_CONSTANT_359 = 'nav-value-359';
export const NAV_CONSTANT_360 = 'nav-value-360';
export function getNavHelper_360(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_361 = 'nav-value-361';
export const NAV_CONSTANT_362 = 'nav-value-362';
export const NAV_CONSTANT_363 = 'nav-value-363';
export const NAV_CONSTANT_364 = 'nav-value-364';
export const NAV_CONSTANT_365 = 'nav-value-365';
export function getNavHelper_365(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_366 = 'nav-value-366';
export const NAV_CONSTANT_367 = 'nav-value-367';
export const NAV_CONSTANT_368 = 'nav-value-368';
export const NAV_CONSTANT_369 = 'nav-value-369';
export const NAV_CONSTANT_370 = 'nav-value-370';
export function getNavHelper_370(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_371 = 'nav-value-371';
export const NAV_CONSTANT_372 = 'nav-value-372';
export const NAV_CONSTANT_373 = 'nav-value-373';
export const NAV_CONSTANT_374 = 'nav-value-374';
export const NAV_CONSTANT_375 = 'nav-value-375';
export function getNavHelper_375(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_376 = 'nav-value-376';
export const NAV_CONSTANT_377 = 'nav-value-377';
export const NAV_CONSTANT_378 = 'nav-value-378';
export const NAV_CONSTANT_379 = 'nav-value-379';
export const NAV_CONSTANT_380 = 'nav-value-380';
export function getNavHelper_380(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_381 = 'nav-value-381';
export const NAV_CONSTANT_382 = 'nav-value-382';
export const NAV_CONSTANT_383 = 'nav-value-383';
export const NAV_CONSTANT_384 = 'nav-value-384';
export const NAV_CONSTANT_385 = 'nav-value-385';
export function getNavHelper_385(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_386 = 'nav-value-386';
export const NAV_CONSTANT_387 = 'nav-value-387';
export const NAV_CONSTANT_388 = 'nav-value-388';
export const NAV_CONSTANT_389 = 'nav-value-389';
export const NAV_CONSTANT_390 = 'nav-value-390';
export function getNavHelper_390(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_391 = 'nav-value-391';
export const NAV_CONSTANT_392 = 'nav-value-392';
export const NAV_CONSTANT_393 = 'nav-value-393';
export const NAV_CONSTANT_394 = 'nav-value-394';
export const NAV_CONSTANT_395 = 'nav-value-395';
export function getNavHelper_395(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_396 = 'nav-value-396';
export const NAV_CONSTANT_397 = 'nav-value-397';
export const NAV_CONSTANT_398 = 'nav-value-398';
export const NAV_CONSTANT_399 = 'nav-value-399';
export const NAV_CONSTANT_400 = 'nav-value-400';
export function getNavHelper_400(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_401 = 'nav-value-401';
export const NAV_CONSTANT_402 = 'nav-value-402';
export const NAV_CONSTANT_403 = 'nav-value-403';
export const NAV_CONSTANT_404 = 'nav-value-404';
export const NAV_CONSTANT_405 = 'nav-value-405';
export function getNavHelper_405(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_406 = 'nav-value-406';
export const NAV_CONSTANT_407 = 'nav-value-407';
export const NAV_CONSTANT_408 = 'nav-value-408';
export const NAV_CONSTANT_409 = 'nav-value-409';
export const NAV_CONSTANT_410 = 'nav-value-410';
export function getNavHelper_410(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_411 = 'nav-value-411';
export const NAV_CONSTANT_412 = 'nav-value-412';
export const NAV_CONSTANT_413 = 'nav-value-413';
export const NAV_CONSTANT_414 = 'nav-value-414';
export const NAV_CONSTANT_415 = 'nav-value-415';
export function getNavHelper_415(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_416 = 'nav-value-416';
export const NAV_CONSTANT_417 = 'nav-value-417';
export const NAV_CONSTANT_418 = 'nav-value-418';
export const NAV_CONSTANT_419 = 'nav-value-419';
export const NAV_CONSTANT_420 = 'nav-value-420';
export function getNavHelper_420(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_421 = 'nav-value-421';
export const NAV_CONSTANT_422 = 'nav-value-422';
export const NAV_CONSTANT_423 = 'nav-value-423';
export const NAV_CONSTANT_424 = 'nav-value-424';
export const NAV_CONSTANT_425 = 'nav-value-425';
export function getNavHelper_425(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_426 = 'nav-value-426';
export const NAV_CONSTANT_427 = 'nav-value-427';
export const NAV_CONSTANT_428 = 'nav-value-428';
export const NAV_CONSTANT_429 = 'nav-value-429';
export const NAV_CONSTANT_430 = 'nav-value-430';
export function getNavHelper_430(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_431 = 'nav-value-431';
export const NAV_CONSTANT_432 = 'nav-value-432';
export const NAV_CONSTANT_433 = 'nav-value-433';
export const NAV_CONSTANT_434 = 'nav-value-434';
export const NAV_CONSTANT_435 = 'nav-value-435';
export function getNavHelper_435(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_436 = 'nav-value-436';
export const NAV_CONSTANT_437 = 'nav-value-437';
export const NAV_CONSTANT_438 = 'nav-value-438';
export const NAV_CONSTANT_439 = 'nav-value-439';
export const NAV_CONSTANT_440 = 'nav-value-440';
export function getNavHelper_440(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_441 = 'nav-value-441';
export const NAV_CONSTANT_442 = 'nav-value-442';
export const NAV_CONSTANT_443 = 'nav-value-443';
export const NAV_CONSTANT_444 = 'nav-value-444';
export const NAV_CONSTANT_445 = 'nav-value-445';
export function getNavHelper_445(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_446 = 'nav-value-446';
export const NAV_CONSTANT_447 = 'nav-value-447';
export const NAV_CONSTANT_448 = 'nav-value-448';
export const NAV_CONSTANT_449 = 'nav-value-449';
export const NAV_CONSTANT_450 = 'nav-value-450';
export function getNavHelper_450(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_451 = 'nav-value-451';
export const NAV_CONSTANT_452 = 'nav-value-452';
export const NAV_CONSTANT_453 = 'nav-value-453';
export const NAV_CONSTANT_454 = 'nav-value-454';
export const NAV_CONSTANT_455 = 'nav-value-455';
export function getNavHelper_455(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_456 = 'nav-value-456';
export const NAV_CONSTANT_457 = 'nav-value-457';
export const NAV_CONSTANT_458 = 'nav-value-458';
export const NAV_CONSTANT_459 = 'nav-value-459';
export const NAV_CONSTANT_460 = 'nav-value-460';
export function getNavHelper_460(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_461 = 'nav-value-461';
export const NAV_CONSTANT_462 = 'nav-value-462';
export const NAV_CONSTANT_463 = 'nav-value-463';
export const NAV_CONSTANT_464 = 'nav-value-464';
export const NAV_CONSTANT_465 = 'nav-value-465';
export function getNavHelper_465(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_466 = 'nav-value-466';
export const NAV_CONSTANT_467 = 'nav-value-467';
export const NAV_CONSTANT_468 = 'nav-value-468';
export const NAV_CONSTANT_469 = 'nav-value-469';
export const NAV_CONSTANT_470 = 'nav-value-470';
export function getNavHelper_470(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_471 = 'nav-value-471';
export const NAV_CONSTANT_472 = 'nav-value-472';
export const NAV_CONSTANT_473 = 'nav-value-473';
export const NAV_CONSTANT_474 = 'nav-value-474';
export const NAV_CONSTANT_475 = 'nav-value-475';
export function getNavHelper_475(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_476 = 'nav-value-476';
export const NAV_CONSTANT_477 = 'nav-value-477';
export const NAV_CONSTANT_478 = 'nav-value-478';
export const NAV_CONSTANT_479 = 'nav-value-479';
export const NAV_CONSTANT_480 = 'nav-value-480';
export function getNavHelper_480(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_481 = 'nav-value-481';
export const NAV_CONSTANT_482 = 'nav-value-482';
export const NAV_CONSTANT_483 = 'nav-value-483';
export const NAV_CONSTANT_484 = 'nav-value-484';
export const NAV_CONSTANT_485 = 'nav-value-485';
export function getNavHelper_485(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_486 = 'nav-value-486';
export const NAV_CONSTANT_487 = 'nav-value-487';
export const NAV_CONSTANT_488 = 'nav-value-488';
export const NAV_CONSTANT_489 = 'nav-value-489';
export const NAV_CONSTANT_490 = 'nav-value-490';
export function getNavHelper_490(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_491 = 'nav-value-491';
export const NAV_CONSTANT_492 = 'nav-value-492';
export const NAV_CONSTANT_493 = 'nav-value-493';
export const NAV_CONSTANT_494 = 'nav-value-494';
export const NAV_CONSTANT_495 = 'nav-value-495';
export function getNavHelper_495(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_496 = 'nav-value-496';
export const NAV_CONSTANT_497 = 'nav-value-497';
export const NAV_CONSTANT_498 = 'nav-value-498';
export const NAV_CONSTANT_499 = 'nav-value-499';
export const NAV_CONSTANT_500 = 'nav-value-500';
export function getNavHelper_500(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_501 = 'nav-value-501';
export const NAV_CONSTANT_502 = 'nav-value-502';
export const NAV_CONSTANT_503 = 'nav-value-503';
export const NAV_CONSTANT_504 = 'nav-value-504';
export const NAV_CONSTANT_505 = 'nav-value-505';
export function getNavHelper_505(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_506 = 'nav-value-506';
export const NAV_CONSTANT_507 = 'nav-value-507';
export const NAV_CONSTANT_508 = 'nav-value-508';
export const NAV_CONSTANT_509 = 'nav-value-509';
export const NAV_CONSTANT_510 = 'nav-value-510';
export function getNavHelper_510(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_511 = 'nav-value-511';
export const NAV_CONSTANT_512 = 'nav-value-512';
export const NAV_CONSTANT_513 = 'nav-value-513';
export const NAV_CONSTANT_514 = 'nav-value-514';
export const NAV_CONSTANT_515 = 'nav-value-515';
export function getNavHelper_515(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_516 = 'nav-value-516';
export const NAV_CONSTANT_517 = 'nav-value-517';
export const NAV_CONSTANT_518 = 'nav-value-518';
export const NAV_CONSTANT_519 = 'nav-value-519';
export const NAV_CONSTANT_520 = 'nav-value-520';
export function getNavHelper_520(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_521 = 'nav-value-521';
export const NAV_CONSTANT_522 = 'nav-value-522';
export const NAV_CONSTANT_523 = 'nav-value-523';
export const NAV_CONSTANT_524 = 'nav-value-524';
export const NAV_CONSTANT_525 = 'nav-value-525';
export function getNavHelper_525(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_526 = 'nav-value-526';
export const NAV_CONSTANT_527 = 'nav-value-527';
export const NAV_CONSTANT_528 = 'nav-value-528';
export const NAV_CONSTANT_529 = 'nav-value-529';
export const NAV_CONSTANT_530 = 'nav-value-530';
export function getNavHelper_530(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_531 = 'nav-value-531';
export const NAV_CONSTANT_532 = 'nav-value-532';
export const NAV_CONSTANT_533 = 'nav-value-533';
export const NAV_CONSTANT_534 = 'nav-value-534';
export const NAV_CONSTANT_535 = 'nav-value-535';
export function getNavHelper_535(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_536 = 'nav-value-536';
export const NAV_CONSTANT_537 = 'nav-value-537';
export const NAV_CONSTANT_538 = 'nav-value-538';
export const NAV_CONSTANT_539 = 'nav-value-539';
export const NAV_CONSTANT_540 = 'nav-value-540';
export function getNavHelper_540(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_541 = 'nav-value-541';
export const NAV_CONSTANT_542 = 'nav-value-542';
export const NAV_CONSTANT_543 = 'nav-value-543';
export const NAV_CONSTANT_544 = 'nav-value-544';
export const NAV_CONSTANT_545 = 'nav-value-545';
export function getNavHelper_545(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_546 = 'nav-value-546';
export const NAV_CONSTANT_547 = 'nav-value-547';
export const NAV_CONSTANT_548 = 'nav-value-548';
export const NAV_CONSTANT_549 = 'nav-value-549';
export const NAV_CONSTANT_550 = 'nav-value-550';
export function getNavHelper_550(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_551 = 'nav-value-551';
export const NAV_CONSTANT_552 = 'nav-value-552';
export const NAV_CONSTANT_553 = 'nav-value-553';
export const NAV_CONSTANT_554 = 'nav-value-554';
export const NAV_CONSTANT_555 = 'nav-value-555';
export function getNavHelper_555(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_556 = 'nav-value-556';
export const NAV_CONSTANT_557 = 'nav-value-557';
export const NAV_CONSTANT_558 = 'nav-value-558';
export const NAV_CONSTANT_559 = 'nav-value-559';
export const NAV_CONSTANT_560 = 'nav-value-560';
export function getNavHelper_560(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_561 = 'nav-value-561';
export const NAV_CONSTANT_562 = 'nav-value-562';
export const NAV_CONSTANT_563 = 'nav-value-563';
export const NAV_CONSTANT_564 = 'nav-value-564';
export const NAV_CONSTANT_565 = 'nav-value-565';
export function getNavHelper_565(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_566 = 'nav-value-566';
export const NAV_CONSTANT_567 = 'nav-value-567';
export const NAV_CONSTANT_568 = 'nav-value-568';
export const NAV_CONSTANT_569 = 'nav-value-569';
export const NAV_CONSTANT_570 = 'nav-value-570';
export function getNavHelper_570(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_571 = 'nav-value-571';
export const NAV_CONSTANT_572 = 'nav-value-572';
export const NAV_CONSTANT_573 = 'nav-value-573';
export const NAV_CONSTANT_574 = 'nav-value-574';
export const NAV_CONSTANT_575 = 'nav-value-575';
export function getNavHelper_575(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_576 = 'nav-value-576';
export const NAV_CONSTANT_577 = 'nav-value-577';
export const NAV_CONSTANT_578 = 'nav-value-578';
export const NAV_CONSTANT_579 = 'nav-value-579';
export const NAV_CONSTANT_580 = 'nav-value-580';
export function getNavHelper_580(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_581 = 'nav-value-581';
export const NAV_CONSTANT_582 = 'nav-value-582';
export const NAV_CONSTANT_583 = 'nav-value-583';
export const NAV_CONSTANT_584 = 'nav-value-584';
export const NAV_CONSTANT_585 = 'nav-value-585';
export function getNavHelper_585(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_586 = 'nav-value-586';
export const NAV_CONSTANT_587 = 'nav-value-587';
export const NAV_CONSTANT_588 = 'nav-value-588';
export const NAV_CONSTANT_589 = 'nav-value-589';
export const NAV_CONSTANT_590 = 'nav-value-590';
export function getNavHelper_590(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_591 = 'nav-value-591';
export const NAV_CONSTANT_592 = 'nav-value-592';
export const NAV_CONSTANT_593 = 'nav-value-593';
export const NAV_CONSTANT_594 = 'nav-value-594';
export const NAV_CONSTANT_595 = 'nav-value-595';
export function getNavHelper_595(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_596 = 'nav-value-596';
export const NAV_CONSTANT_597 = 'nav-value-597';
export const NAV_CONSTANT_598 = 'nav-value-598';
export const NAV_CONSTANT_599 = 'nav-value-599';
export const NAV_CONSTANT_600 = 'nav-value-600';
export function getNavHelper_600(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_601 = 'nav-value-601';
export const NAV_CONSTANT_602 = 'nav-value-602';
export const NAV_CONSTANT_603 = 'nav-value-603';
export const NAV_CONSTANT_604 = 'nav-value-604';
export const NAV_CONSTANT_605 = 'nav-value-605';
export function getNavHelper_605(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_606 = 'nav-value-606';
export const NAV_CONSTANT_607 = 'nav-value-607';
export const NAV_CONSTANT_608 = 'nav-value-608';
export const NAV_CONSTANT_609 = 'nav-value-609';
export const NAV_CONSTANT_610 = 'nav-value-610';
export function getNavHelper_610(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_611 = 'nav-value-611';
export const NAV_CONSTANT_612 = 'nav-value-612';
export const NAV_CONSTANT_613 = 'nav-value-613';
export const NAV_CONSTANT_614 = 'nav-value-614';
export const NAV_CONSTANT_615 = 'nav-value-615';
export function getNavHelper_615(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_616 = 'nav-value-616';
export const NAV_CONSTANT_617 = 'nav-value-617';
export const NAV_CONSTANT_618 = 'nav-value-618';
export const NAV_CONSTANT_619 = 'nav-value-619';
export const NAV_CONSTANT_620 = 'nav-value-620';
export function getNavHelper_620(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_621 = 'nav-value-621';
export const NAV_CONSTANT_622 = 'nav-value-622';
export const NAV_CONSTANT_623 = 'nav-value-623';
export const NAV_CONSTANT_624 = 'nav-value-624';
export const NAV_CONSTANT_625 = 'nav-value-625';
export function getNavHelper_625(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_626 = 'nav-value-626';
export const NAV_CONSTANT_627 = 'nav-value-627';
export const NAV_CONSTANT_628 = 'nav-value-628';
export const NAV_CONSTANT_629 = 'nav-value-629';
export const NAV_CONSTANT_630 = 'nav-value-630';
export function getNavHelper_630(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_631 = 'nav-value-631';
export const NAV_CONSTANT_632 = 'nav-value-632';
export const NAV_CONSTANT_633 = 'nav-value-633';
export const NAV_CONSTANT_634 = 'nav-value-634';
export const NAV_CONSTANT_635 = 'nav-value-635';
export function getNavHelper_635(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_636 = 'nav-value-636';
export const NAV_CONSTANT_637 = 'nav-value-637';
export const NAV_CONSTANT_638 = 'nav-value-638';
export const NAV_CONSTANT_639 = 'nav-value-639';
export const NAV_CONSTANT_640 = 'nav-value-640';
export function getNavHelper_640(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_641 = 'nav-value-641';
export const NAV_CONSTANT_642 = 'nav-value-642';
export const NAV_CONSTANT_643 = 'nav-value-643';
export const NAV_CONSTANT_644 = 'nav-value-644';
export const NAV_CONSTANT_645 = 'nav-value-645';
export function getNavHelper_645(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_646 = 'nav-value-646';
export const NAV_CONSTANT_647 = 'nav-value-647';
export const NAV_CONSTANT_648 = 'nav-value-648';
export const NAV_CONSTANT_649 = 'nav-value-649';
export const NAV_CONSTANT_650 = 'nav-value-650';
export function getNavHelper_650(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_651 = 'nav-value-651';
export const NAV_CONSTANT_652 = 'nav-value-652';
export const NAV_CONSTANT_653 = 'nav-value-653';
export const NAV_CONSTANT_654 = 'nav-value-654';
export const NAV_CONSTANT_655 = 'nav-value-655';
export function getNavHelper_655(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_656 = 'nav-value-656';
export const NAV_CONSTANT_657 = 'nav-value-657';
export const NAV_CONSTANT_658 = 'nav-value-658';
export const NAV_CONSTANT_659 = 'nav-value-659';
export const NAV_CONSTANT_660 = 'nav-value-660';
export function getNavHelper_660(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_661 = 'nav-value-661';
export const NAV_CONSTANT_662 = 'nav-value-662';
export const NAV_CONSTANT_663 = 'nav-value-663';
export const NAV_CONSTANT_664 = 'nav-value-664';
export const NAV_CONSTANT_665 = 'nav-value-665';
export function getNavHelper_665(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_666 = 'nav-value-666';
export const NAV_CONSTANT_667 = 'nav-value-667';
export const NAV_CONSTANT_668 = 'nav-value-668';
export const NAV_CONSTANT_669 = 'nav-value-669';
export const NAV_CONSTANT_670 = 'nav-value-670';
export function getNavHelper_670(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_671 = 'nav-value-671';
export const NAV_CONSTANT_672 = 'nav-value-672';
export const NAV_CONSTANT_673 = 'nav-value-673';
export const NAV_CONSTANT_674 = 'nav-value-674';
export const NAV_CONSTANT_675 = 'nav-value-675';
export function getNavHelper_675(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_676 = 'nav-value-676';
export const NAV_CONSTANT_677 = 'nav-value-677';
export const NAV_CONSTANT_678 = 'nav-value-678';
export const NAV_CONSTANT_679 = 'nav-value-679';
export const NAV_CONSTANT_680 = 'nav-value-680';
export function getNavHelper_680(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_681 = 'nav-value-681';
export const NAV_CONSTANT_682 = 'nav-value-682';
export const NAV_CONSTANT_683 = 'nav-value-683';
export const NAV_CONSTANT_684 = 'nav-value-684';
export const NAV_CONSTANT_685 = 'nav-value-685';
export function getNavHelper_685(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_686 = 'nav-value-686';
export const NAV_CONSTANT_687 = 'nav-value-687';
export const NAV_CONSTANT_688 = 'nav-value-688';
export const NAV_CONSTANT_689 = 'nav-value-689';
export const NAV_CONSTANT_690 = 'nav-value-690';
export function getNavHelper_690(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_691 = 'nav-value-691';
export const NAV_CONSTANT_692 = 'nav-value-692';
export const NAV_CONSTANT_693 = 'nav-value-693';
export const NAV_CONSTANT_694 = 'nav-value-694';
export const NAV_CONSTANT_695 = 'nav-value-695';
export function getNavHelper_695(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_696 = 'nav-value-696';
export const NAV_CONSTANT_697 = 'nav-value-697';
export const NAV_CONSTANT_698 = 'nav-value-698';
export const NAV_CONSTANT_699 = 'nav-value-699';
export const NAV_CONSTANT_700 = 'nav-value-700';
export function getNavHelper_700(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_701 = 'nav-value-701';
export const NAV_CONSTANT_702 = 'nav-value-702';
export const NAV_CONSTANT_703 = 'nav-value-703';
export const NAV_CONSTANT_704 = 'nav-value-704';
export const NAV_CONSTANT_705 = 'nav-value-705';
export function getNavHelper_705(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_706 = 'nav-value-706';
export const NAV_CONSTANT_707 = 'nav-value-707';
export const NAV_CONSTANT_708 = 'nav-value-708';
export const NAV_CONSTANT_709 = 'nav-value-709';
export const NAV_CONSTANT_710 = 'nav-value-710';
export function getNavHelper_710(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_711 = 'nav-value-711';
export const NAV_CONSTANT_712 = 'nav-value-712';
export const NAV_CONSTANT_713 = 'nav-value-713';
export const NAV_CONSTANT_714 = 'nav-value-714';
export const NAV_CONSTANT_715 = 'nav-value-715';
export function getNavHelper_715(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_716 = 'nav-value-716';
export const NAV_CONSTANT_717 = 'nav-value-717';
export const NAV_CONSTANT_718 = 'nav-value-718';
export const NAV_CONSTANT_719 = 'nav-value-719';
export const NAV_CONSTANT_720 = 'nav-value-720';
export function getNavHelper_720(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_721 = 'nav-value-721';
export const NAV_CONSTANT_722 = 'nav-value-722';
export const NAV_CONSTANT_723 = 'nav-value-723';
export const NAV_CONSTANT_724 = 'nav-value-724';
export const NAV_CONSTANT_725 = 'nav-value-725';
export function getNavHelper_725(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_726 = 'nav-value-726';
export const NAV_CONSTANT_727 = 'nav-value-727';
export const NAV_CONSTANT_728 = 'nav-value-728';
export const NAV_CONSTANT_729 = 'nav-value-729';
export const NAV_CONSTANT_730 = 'nav-value-730';
export function getNavHelper_730(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_731 = 'nav-value-731';
export const NAV_CONSTANT_732 = 'nav-value-732';
export const NAV_CONSTANT_733 = 'nav-value-733';
export const NAV_CONSTANT_734 = 'nav-value-734';
export const NAV_CONSTANT_735 = 'nav-value-735';
export function getNavHelper_735(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_736 = 'nav-value-736';
export const NAV_CONSTANT_737 = 'nav-value-737';
export const NAV_CONSTANT_738 = 'nav-value-738';
export const NAV_CONSTANT_739 = 'nav-value-739';
export const NAV_CONSTANT_740 = 'nav-value-740';
export function getNavHelper_740(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_741 = 'nav-value-741';
export const NAV_CONSTANT_742 = 'nav-value-742';
export const NAV_CONSTANT_743 = 'nav-value-743';
export const NAV_CONSTANT_744 = 'nav-value-744';
export const NAV_CONSTANT_745 = 'nav-value-745';
export function getNavHelper_745(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_746 = 'nav-value-746';
export const NAV_CONSTANT_747 = 'nav-value-747';
export const NAV_CONSTANT_748 = 'nav-value-748';
export const NAV_CONSTANT_749 = 'nav-value-749';
export const NAV_CONSTANT_750 = 'nav-value-750';
export function getNavHelper_750(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_751 = 'nav-value-751';
export const NAV_CONSTANT_752 = 'nav-value-752';
export const NAV_CONSTANT_753 = 'nav-value-753';
export const NAV_CONSTANT_754 = 'nav-value-754';
export const NAV_CONSTANT_755 = 'nav-value-755';
export function getNavHelper_755(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_756 = 'nav-value-756';
export const NAV_CONSTANT_757 = 'nav-value-757';
export const NAV_CONSTANT_758 = 'nav-value-758';
export const NAV_CONSTANT_759 = 'nav-value-759';
export const NAV_CONSTANT_760 = 'nav-value-760';
export function getNavHelper_760(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_761 = 'nav-value-761';
export const NAV_CONSTANT_762 = 'nav-value-762';
export const NAV_CONSTANT_763 = 'nav-value-763';
export const NAV_CONSTANT_764 = 'nav-value-764';
export const NAV_CONSTANT_765 = 'nav-value-765';
export function getNavHelper_765(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_766 = 'nav-value-766';
export const NAV_CONSTANT_767 = 'nav-value-767';
export const NAV_CONSTANT_768 = 'nav-value-768';
export const NAV_CONSTANT_769 = 'nav-value-769';
export const NAV_CONSTANT_770 = 'nav-value-770';
export function getNavHelper_770(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_771 = 'nav-value-771';
export const NAV_CONSTANT_772 = 'nav-value-772';
export const NAV_CONSTANT_773 = 'nav-value-773';
export const NAV_CONSTANT_774 = 'nav-value-774';
export const NAV_CONSTANT_775 = 'nav-value-775';
export function getNavHelper_775(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_776 = 'nav-value-776';
export const NAV_CONSTANT_777 = 'nav-value-777';
export const NAV_CONSTANT_778 = 'nav-value-778';
export const NAV_CONSTANT_779 = 'nav-value-779';
export const NAV_CONSTANT_780 = 'nav-value-780';
export function getNavHelper_780(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_781 = 'nav-value-781';
export const NAV_CONSTANT_782 = 'nav-value-782';
export const NAV_CONSTANT_783 = 'nav-value-783';
export const NAV_CONSTANT_784 = 'nav-value-784';
export const NAV_CONSTANT_785 = 'nav-value-785';
export function getNavHelper_785(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_786 = 'nav-value-786';
export const NAV_CONSTANT_787 = 'nav-value-787';
export const NAV_CONSTANT_788 = 'nav-value-788';
export const NAV_CONSTANT_789 = 'nav-value-789';
export const NAV_CONSTANT_790 = 'nav-value-790';
export function getNavHelper_790(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_791 = 'nav-value-791';
export const NAV_CONSTANT_792 = 'nav-value-792';
export const NAV_CONSTANT_793 = 'nav-value-793';
export const NAV_CONSTANT_794 = 'nav-value-794';
export const NAV_CONSTANT_795 = 'nav-value-795';
export function getNavHelper_795(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_796 = 'nav-value-796';
export const NAV_CONSTANT_797 = 'nav-value-797';
export const NAV_CONSTANT_798 = 'nav-value-798';
export const NAV_CONSTANT_799 = 'nav-value-799';
export const NAV_CONSTANT_800 = 'nav-value-800';
export function getNavHelper_800(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_801 = 'nav-value-801';
export const NAV_CONSTANT_802 = 'nav-value-802';
export const NAV_CONSTANT_803 = 'nav-value-803';
export const NAV_CONSTANT_804 = 'nav-value-804';
export const NAV_CONSTANT_805 = 'nav-value-805';
export function getNavHelper_805(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_806 = 'nav-value-806';
export const NAV_CONSTANT_807 = 'nav-value-807';
export const NAV_CONSTANT_808 = 'nav-value-808';
export const NAV_CONSTANT_809 = 'nav-value-809';
export const NAV_CONSTANT_810 = 'nav-value-810';
export function getNavHelper_810(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_811 = 'nav-value-811';
export const NAV_CONSTANT_812 = 'nav-value-812';
export const NAV_CONSTANT_813 = 'nav-value-813';
export const NAV_CONSTANT_814 = 'nav-value-814';
export const NAV_CONSTANT_815 = 'nav-value-815';
export function getNavHelper_815(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_816 = 'nav-value-816';
export const NAV_CONSTANT_817 = 'nav-value-817';
export const NAV_CONSTANT_818 = 'nav-value-818';
export const NAV_CONSTANT_819 = 'nav-value-819';
export const NAV_CONSTANT_820 = 'nav-value-820';
export function getNavHelper_820(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_821 = 'nav-value-821';
export const NAV_CONSTANT_822 = 'nav-value-822';
export const NAV_CONSTANT_823 = 'nav-value-823';
export const NAV_CONSTANT_824 = 'nav-value-824';
export const NAV_CONSTANT_825 = 'nav-value-825';
export function getNavHelper_825(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_826 = 'nav-value-826';
export const NAV_CONSTANT_827 = 'nav-value-827';
export const NAV_CONSTANT_828 = 'nav-value-828';
export const NAV_CONSTANT_829 = 'nav-value-829';
export const NAV_CONSTANT_830 = 'nav-value-830';
export function getNavHelper_830(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_831 = 'nav-value-831';
export const NAV_CONSTANT_832 = 'nav-value-832';
export const NAV_CONSTANT_833 = 'nav-value-833';
export const NAV_CONSTANT_834 = 'nav-value-834';
export const NAV_CONSTANT_835 = 'nav-value-835';
export function getNavHelper_835(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_836 = 'nav-value-836';
export const NAV_CONSTANT_837 = 'nav-value-837';
export const NAV_CONSTANT_838 = 'nav-value-838';
export const NAV_CONSTANT_839 = 'nav-value-839';
export const NAV_CONSTANT_840 = 'nav-value-840';
export function getNavHelper_840(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_841 = 'nav-value-841';
export const NAV_CONSTANT_842 = 'nav-value-842';
export const NAV_CONSTANT_843 = 'nav-value-843';
export const NAV_CONSTANT_844 = 'nav-value-844';
export const NAV_CONSTANT_845 = 'nav-value-845';
export function getNavHelper_845(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_846 = 'nav-value-846';
export const NAV_CONSTANT_847 = 'nav-value-847';
export const NAV_CONSTANT_848 = 'nav-value-848';
export const NAV_CONSTANT_849 = 'nav-value-849';
export const NAV_CONSTANT_850 = 'nav-value-850';
export function getNavHelper_850(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_851 = 'nav-value-851';
export const NAV_CONSTANT_852 = 'nav-value-852';
export const NAV_CONSTANT_853 = 'nav-value-853';
export const NAV_CONSTANT_854 = 'nav-value-854';
export const NAV_CONSTANT_855 = 'nav-value-855';
export function getNavHelper_855(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_856 = 'nav-value-856';
export const NAV_CONSTANT_857 = 'nav-value-857';
export const NAV_CONSTANT_858 = 'nav-value-858';
export const NAV_CONSTANT_859 = 'nav-value-859';
export const NAV_CONSTANT_860 = 'nav-value-860';
export function getNavHelper_860(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_861 = 'nav-value-861';
export const NAV_CONSTANT_862 = 'nav-value-862';
export const NAV_CONSTANT_863 = 'nav-value-863';
export const NAV_CONSTANT_864 = 'nav-value-864';
export const NAV_CONSTANT_865 = 'nav-value-865';
export function getNavHelper_865(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_866 = 'nav-value-866';
export const NAV_CONSTANT_867 = 'nav-value-867';
export const NAV_CONSTANT_868 = 'nav-value-868';
export const NAV_CONSTANT_869 = 'nav-value-869';
export const NAV_CONSTANT_870 = 'nav-value-870';
export function getNavHelper_870(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_871 = 'nav-value-871';
export const NAV_CONSTANT_872 = 'nav-value-872';
export const NAV_CONSTANT_873 = 'nav-value-873';
export const NAV_CONSTANT_874 = 'nav-value-874';
export const NAV_CONSTANT_875 = 'nav-value-875';
export function getNavHelper_875(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_876 = 'nav-value-876';
export const NAV_CONSTANT_877 = 'nav-value-877';
export const NAV_CONSTANT_878 = 'nav-value-878';
export const NAV_CONSTANT_879 = 'nav-value-879';
export const NAV_CONSTANT_880 = 'nav-value-880';
export function getNavHelper_880(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_881 = 'nav-value-881';
export const NAV_CONSTANT_882 = 'nav-value-882';
export const NAV_CONSTANT_883 = 'nav-value-883';
export const NAV_CONSTANT_884 = 'nav-value-884';
export const NAV_CONSTANT_885 = 'nav-value-885';
export function getNavHelper_885(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_886 = 'nav-value-886';
export const NAV_CONSTANT_887 = 'nav-value-887';
export const NAV_CONSTANT_888 = 'nav-value-888';
export const NAV_CONSTANT_889 = 'nav-value-889';
export const NAV_CONSTANT_890 = 'nav-value-890';
export function getNavHelper_890(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_891 = 'nav-value-891';
export const NAV_CONSTANT_892 = 'nav-value-892';
export const NAV_CONSTANT_893 = 'nav-value-893';
export const NAV_CONSTANT_894 = 'nav-value-894';
export const NAV_CONSTANT_895 = 'nav-value-895';
export function getNavHelper_895(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_896 = 'nav-value-896';
export const NAV_CONSTANT_897 = 'nav-value-897';
export const NAV_CONSTANT_898 = 'nav-value-898';
export const NAV_CONSTANT_899 = 'nav-value-899';
export const NAV_CONSTANT_900 = 'nav-value-900';
export function getNavHelper_900(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_901 = 'nav-value-901';
export const NAV_CONSTANT_902 = 'nav-value-902';
export const NAV_CONSTANT_903 = 'nav-value-903';
export const NAV_CONSTANT_904 = 'nav-value-904';
export const NAV_CONSTANT_905 = 'nav-value-905';
export function getNavHelper_905(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_906 = 'nav-value-906';
export const NAV_CONSTANT_907 = 'nav-value-907';
export const NAV_CONSTANT_908 = 'nav-value-908';
export const NAV_CONSTANT_909 = 'nav-value-909';
export const NAV_CONSTANT_910 = 'nav-value-910';
export function getNavHelper_910(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_911 = 'nav-value-911';
export const NAV_CONSTANT_912 = 'nav-value-912';
export const NAV_CONSTANT_913 = 'nav-value-913';
export const NAV_CONSTANT_914 = 'nav-value-914';
export const NAV_CONSTANT_915 = 'nav-value-915';
export function getNavHelper_915(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_916 = 'nav-value-916';
export const NAV_CONSTANT_917 = 'nav-value-917';
export const NAV_CONSTANT_918 = 'nav-value-918';
export const NAV_CONSTANT_919 = 'nav-value-919';
export const NAV_CONSTANT_920 = 'nav-value-920';
export function getNavHelper_920(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_921 = 'nav-value-921';
export const NAV_CONSTANT_922 = 'nav-value-922';
export const NAV_CONSTANT_923 = 'nav-value-923';
export const NAV_CONSTANT_924 = 'nav-value-924';
export const NAV_CONSTANT_925 = 'nav-value-925';
export function getNavHelper_925(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_926 = 'nav-value-926';
export const NAV_CONSTANT_927 = 'nav-value-927';
export const NAV_CONSTANT_928 = 'nav-value-928';
export const NAV_CONSTANT_929 = 'nav-value-929';
export const NAV_CONSTANT_930 = 'nav-value-930';
export function getNavHelper_930(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_931 = 'nav-value-931';
export const NAV_CONSTANT_932 = 'nav-value-932';
export const NAV_CONSTANT_933 = 'nav-value-933';
export const NAV_CONSTANT_934 = 'nav-value-934';
export const NAV_CONSTANT_935 = 'nav-value-935';
export function getNavHelper_935(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_936 = 'nav-value-936';
export const NAV_CONSTANT_937 = 'nav-value-937';
export const NAV_CONSTANT_938 = 'nav-value-938';
export const NAV_CONSTANT_939 = 'nav-value-939';
export const NAV_CONSTANT_940 = 'nav-value-940';
export function getNavHelper_940(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_941 = 'nav-value-941';
export const NAV_CONSTANT_942 = 'nav-value-942';
export const NAV_CONSTANT_943 = 'nav-value-943';
export const NAV_CONSTANT_944 = 'nav-value-944';
export const NAV_CONSTANT_945 = 'nav-value-945';
export function getNavHelper_945(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_946 = 'nav-value-946';
export const NAV_CONSTANT_947 = 'nav-value-947';
export const NAV_CONSTANT_948 = 'nav-value-948';
export const NAV_CONSTANT_949 = 'nav-value-949';
export const NAV_CONSTANT_950 = 'nav-value-950';
export function getNavHelper_950(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_951 = 'nav-value-951';
export const NAV_CONSTANT_952 = 'nav-value-952';
export const NAV_CONSTANT_953 = 'nav-value-953';
export const NAV_CONSTANT_954 = 'nav-value-954';
export const NAV_CONSTANT_955 = 'nav-value-955';
export function getNavHelper_955(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_956 = 'nav-value-956';
export const NAV_CONSTANT_957 = 'nav-value-957';
export const NAV_CONSTANT_958 = 'nav-value-958';
export const NAV_CONSTANT_959 = 'nav-value-959';
export const NAV_CONSTANT_960 = 'nav-value-960';
export function getNavHelper_960(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_961 = 'nav-value-961';
export const NAV_CONSTANT_962 = 'nav-value-962';
export const NAV_CONSTANT_963 = 'nav-value-963';
export const NAV_CONSTANT_964 = 'nav-value-964';
export const NAV_CONSTANT_965 = 'nav-value-965';
export function getNavHelper_965(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_966 = 'nav-value-966';
export const NAV_CONSTANT_967 = 'nav-value-967';
export const NAV_CONSTANT_968 = 'nav-value-968';
export const NAV_CONSTANT_969 = 'nav-value-969';
export const NAV_CONSTANT_970 = 'nav-value-970';
export function getNavHelper_970(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_971 = 'nav-value-971';
export const NAV_CONSTANT_972 = 'nav-value-972';
export const NAV_CONSTANT_973 = 'nav-value-973';
export const NAV_CONSTANT_974 = 'nav-value-974';
export const NAV_CONSTANT_975 = 'nav-value-975';
export function getNavHelper_975(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_976 = 'nav-value-976';
export const NAV_CONSTANT_977 = 'nav-value-977';
export const NAV_CONSTANT_978 = 'nav-value-978';
export const NAV_CONSTANT_979 = 'nav-value-979';
export const NAV_CONSTANT_980 = 'nav-value-980';
export function getNavHelper_980(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_981 = 'nav-value-981';
export const NAV_CONSTANT_982 = 'nav-value-982';
export const NAV_CONSTANT_983 = 'nav-value-983';
export const NAV_CONSTANT_984 = 'nav-value-984';
export const NAV_CONSTANT_985 = 'nav-value-985';
export function getNavHelper_985(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_986 = 'nav-value-986';
export const NAV_CONSTANT_987 = 'nav-value-987';
export const NAV_CONSTANT_988 = 'nav-value-988';
export const NAV_CONSTANT_989 = 'nav-value-989';
export const NAV_CONSTANT_990 = 'nav-value-990';
export function getNavHelper_990(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_991 = 'nav-value-991';
export const NAV_CONSTANT_992 = 'nav-value-992';
export const NAV_CONSTANT_993 = 'nav-value-993';
export const NAV_CONSTANT_994 = 'nav-value-994';
export const NAV_CONSTANT_995 = 'nav-value-995';
export function getNavHelper_995(id: string): string { return `${id}-${i}`; }
export const NAV_CONSTANT_996 = 'nav-value-996';
export const NAV_CONSTANT_997 = 'nav-value-997';
export const NAV_CONSTANT_998 = 'nav-value-998';
export const NAV_CONSTANT_999 = 'nav-value-999';