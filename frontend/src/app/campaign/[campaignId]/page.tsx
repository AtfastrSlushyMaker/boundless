import { GameScreen } from "@/components/GameScreen";

type PageProps = { params: Promise<{ campaignId: string }>; searchParams: Promise<{ branch?: string }> };

export default async function CampaignPage({ params, searchParams }: PageProps) {
  const [{ campaignId }, query] = await Promise.all([params, searchParams]);
  return <GameScreen campaignId={campaignId} requestedBranchId={query.branch} />;
}
