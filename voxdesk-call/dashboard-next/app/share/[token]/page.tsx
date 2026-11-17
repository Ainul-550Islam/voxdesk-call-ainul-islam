// File: dashboard-next/app/share/[token]/page.tsx — route entry for the public share link
// The server half: unwrap the param, hand it to the client component as a string.

import ShareLinkView from "./share-link-view";

export default async function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <ShareLinkView token={token} />;
}
