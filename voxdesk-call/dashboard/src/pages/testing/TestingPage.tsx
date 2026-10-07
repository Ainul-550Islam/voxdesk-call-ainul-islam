/**
 * dashboard/src/pages/testing/TestingPage.tsx
 * Main testing workspace for selecting pinned agent versions, executing LLM/simulation tests,
 * and viewing evidence-backed QA evaluation results.
 */

import React from 'react';
import { AgentPlayground } from '../product/AgentPlayground';

export function TestingPage() {
  return <AgentPlayground />;
}

export default TestingPage;
