import React from 'react';
import { PublicFooter } from '../../components/layout/PublicFooter';
import { PublicHeader } from '../../components/layout/PublicHeader';
import { DevelopersAPI } from './DevelopersAPI';
import { DevelopersChangelog } from './DevelopersChangelog';
import { DevelopersGuides } from './DevelopersGuides';
import { DevelopersHero } from './DevelopersHero';
import { DevelopersSDKs } from './DevelopersSDKs';
import { DevelopersWebhooks } from './DevelopersWebhooks';

export function DevelopersPage() {
  return (
    <div className="min-h-screen bg-black text-white">
      <PublicHeader />
      <main>
        <DevelopersHero />
        <DevelopersAPI />
        <DevelopersSDKs />
        <DevelopersWebhooks />
        <DevelopersGuides />
        <DevelopersChangelog />
      </main>
      <PublicFooter />
    </div>
  );
}

export default DevelopersPage;
