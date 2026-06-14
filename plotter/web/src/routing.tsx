import {
  BrowserRouter,
  Link as ReactRouterLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate as useReactRouterNavigate,
} from 'react-router-dom'
import type { ComponentProps } from 'react'

type SearchValue = string | number | boolean | null | undefined
type SearchRecord = Record<string, SearchValue>

type RouterState = {
  location: {
    pathname: string
    search: Record<string, string | undefined>
  }
}

type NavigateOptions = {
  to: string
  search?: SearchRecord
  replace?: boolean
}

type LinkProps = Omit<ComponentProps<typeof ReactRouterLink>, 'to'> & {
  to: string
  search?: SearchRecord
  activeOptions?: unknown
}

function searchToQuery(search?: SearchRecord) {
  if (!search) return ''
  const params = new URLSearchParams()
  Object.entries(search).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    params.set(key, String(value))
  })
  const query = params.toString()
  return query ? `?${query}` : ''
}

function buildPath(to: string, search?: SearchRecord) {
  return `${to || '/'}${searchToQuery(search)}`
}

export function useNavigate() {
  const navigate = useReactRouterNavigate()
  return (options: NavigateOptions) => {
    navigate(buildPath(options.to, options.search), { replace: Boolean(options.replace) })
  }
}

export function useRouterState<T>({ select }: { select: (state: RouterState) => T }) {
  const location = useLocation()
  const search = Object.fromEntries(new URLSearchParams(location.search).entries())
  return select({
    location: {
      pathname: location.pathname,
      search,
    },
  })
}

export function Link({ to, search, activeOptions: _activeOptions, ...props }: LinkProps) {
  return <ReactRouterLink {...props} to={buildPath(to, search)} />
}

export { BrowserRouter, Outlet, Route, Routes }
