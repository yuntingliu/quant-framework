import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { Widget } from "@/widgets/Widget"

interface ExampleScore {
  symbol: string
  score: number
  asof_date: string
  status: string
}

export function ExampleScoreWidget() {
  const query = useQuery({
    queryKey: ["example-score", "000001.SZ"],
    queryFn: () => api.get<ExampleScore>("/examples/score?symbol=000001.SZ"),
  })

  return (
    <Widget
      title="Example Score"
      loading={query.isLoading}
      error={query.error instanceof Error ? query.error.message : undefined}
      onRetry={() => query.refetch()}
    >
      {query.data && (
        <dl>
          <dt>{query.data.symbol}</dt>
          <dd>{query.data.score.toFixed(2)}</dd>
          <dd>{query.data.asof_date}</dd>
        </dl>
      )}
    </Widget>
  )
}
