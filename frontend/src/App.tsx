import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

import { LiveSessionPage } from './views/LiveSessionPage'

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      // The page decides when data is refreshed; a window regaining focus is
      // not a reason to refetch a session.
      queries: { refetchOnWindowFocus: false },
    },
  })
}

export function App() {
  // Created once per mounted app, not once per module, so nothing fetched by
  // one instance (or one test) can leak into another.
  const [queryClient] = useState(createQueryClient)

  return (
    <QueryClientProvider client={queryClient}>
      <LiveSessionPage />
    </QueryClientProvider>
  )
}
