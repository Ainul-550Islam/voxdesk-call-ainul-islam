import React from 'react';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { HomeHero } from './HomeHero';
import { HomeTrust } from './HomeTrust';
import { HomeCapabilities } from './HomeCapabilities';
import { HomeArchitecture } from './HomeArchitecture';
import { HomeVoiceDemo } from './HomeVoiceDemo';
import { HomeUseCases } from './HomeUseCases';
import { HomeDeveloper } from './HomeDeveloper';
import { HomeAnalyticsPreview } from './HomeAnalyticsPreview';
import { HomeSecurity } from './HomeSecurity';
import { HomeFinalCTA } from './HomeFinalCTA';
import { useHomeData } from '../../hooks/useHomeData';
export function HomePage(){
  const {homeData,analytics,loadingState,error,retry}=useHomeData();
  if (loadingState==='loading'){
    return (<div className="min-h-screen bg-black"><PublicHeader /><div className="mx-auto max-w-7xl px-4 py-20 sm:px-6 lg:px-8"><div className="animate-pulse space-y-8"><div className="h-32 rounded-2xl bg-white/10" /><div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">{[1,2,3,4].map((i)=><div key={i} className="h-48 rounded-2xl bg-white/5" />)}</div></div></div><PublicFooter /></div>);
  }
  if (loadingState==='error' || loadingState==='not_configured'){
    const title = loadingState === 'not_configured' ? 'Home content is not configured' : 'Failed to load home data';
    return (<div className="min-h-screen bg-black"><PublicHeader /><main className="mx-auto max-w-7xl px-4 py-20 text-center"><div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-8" role="alert"><h1 className="text-lg font-medium text-red-300">{title}</h1><p className="mt-2 text-sm text-red-300/70">{error}</p><button type="button" onClick={retry} className="mt-6 rounded-xl bg-white px-5 py-2.5 text-sm font-medium text-black hover:bg-white/90">Retry</button></div></main><PublicFooter /></div>);
  }
  return (<div className="min-h-screen bg-black text-white"><PublicHeader /><main><HomeHero /><HomeTrust /><HomeCapabilities capabilities={homeData?.capabilities} /><HomeArchitecture /><HomeVoiceDemo /><HomeUseCases useCases={homeData?.use_cases} /><HomeDeveloper features={homeData?.developer_features} /><HomeAnalyticsPreview analytics={analytics} /><HomeSecurity items={homeData?.security_items} /><HomeFinalCTA /></main><PublicFooter /></div>);
}
export default HomePage;
