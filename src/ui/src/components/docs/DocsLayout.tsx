'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ChevronDown, ChevronRight, Loader2, Search } from 'lucide-react'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
	} from '@/components/ui/dropdown-menu'
import { NotificationBell } from '@/components/ui/notification-bell'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/lib/stores/theme'
import { BRAND_LOGO_SMALL_SRC, BRAND_LOGO_SMALL_SRC_INVERTED } from '@/lib/constants/assets'
import { fetchDocsIndex, searchDocs } from '@/lib/docs'
import type {
  DocsDirNode,
  DocsFileNode,
  DocsIndexResponse,
  DocsNode,
  DocsSearchResult,
  MarkdownHeading,
} from '@/lib/docs/types'
import { DocsContent } from './DocsContent'
import { DocsToc } from './DocsToc'

function findNodeByPath(root: DocsDirNode, path: string): DocsNode | null {
  if (!path) return root
  const parts = path.split('/').filter(Boolean)

  let current: DocsNode = root
  let currentPath = ''

  for (const part of parts) {
    currentPath = currentPath ? `${currentPath}/${part}` : part
    if (current.type !== 'dir' || !current.children) return null
    const nextNode: DocsNode | undefined = current.children.find(
      (child) => child.path === currentPath
    )
    if (!nextNode) return null
    current = nextNode
  }

  return current
}

interface DocsLayoutProps {
  slug?: string[]
}

export function DocsLayout({ slug = [] }: DocsLayoutProps) {
  const router = useRouter()
  const resolvedTheme = useThemeStore((state) => state.resolvedTheme)
  const brandLogoSrc = resolvedTheme === 'dark' ? BRAND_LOGO_SMALL_SRC_INVERTED : BRAND_LOGO_SMALL_SRC

  const [index, setIndex] = useState<DocsIndexResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [toc, setToc] = useState<MarkdownHeading[]>([])

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<DocsSearchResult[]>([])
  const [searchOpen, setSearchOpen] = useState(false)
  const searchTimer = useRef<number | null>(null)
  const headerRef = useRef<HTMLElement | null>(null)
  const tabsRef = useRef<HTMLDivElement | null>(null)

  const decodedSegments = useMemo(() => {
    return slug.map((segment) => {
      try {
        return decodeURIComponent(segment)
      } catch {
        return segment
      }
    })
  }, [slug])

  const currentPath = useMemo(() => decodedSegments.join('/'), [decodedSegments])

  useEffect(() => {
    async function loadIndex() {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchDocsIndex()
        setIndex(data)
      } catch (err) {
        console.error('Failed to load docs index:', err)
        setError('Failed to load documentation index.')
      } finally {
        setLoading(false)
      }
    }

    loadIndex()
  }, [])

  const { languages, rootFiles } = useMemo(() => {
    const dirs: DocsDirNode[] = []
    const files: DocsFileNode[] = []
    for (const child of index?.root.children || []) {
      if (child.type === 'dir') dirs.push(child as DocsDirNode)
      if (child.type === 'file') files.push(child as DocsFileNode)
    }
    return { languages: dirs, rootFiles: files }
  }, [index])

  const languageFromUrl = useMemo(() => {
    if (!index) return null
    const candidate = decodedSegments[0]
    if (!candidate) return null
    const languageNode = languages.find((d) => d.name === candidate || d.path === candidate)
    return languageNode ? languageNode.name : null
  }, [decodedSegments, index, languages])

  const currentLanguageKey = languageFromUrl || '__root__'

  const languageNode = useMemo(() => {
    if (!index) return null
    if (currentLanguageKey === '__root__') return index.root
    return languages.find((d) => d.name === currentLanguageKey) || null
  }, [currentLanguageKey, index, languages])

  const sections = useMemo(() => {
    if (!languageNode || languageNode.type !== 'dir') return []
    const dirs: DocsDirNode[] = []
    for (const child of languageNode.children || []) {
      if (child.type === 'dir') dirs.push(child as DocsDirNode)
    }
    return dirs
  }, [languageNode])

  const currentSectionName = useMemo(() => {
    if (currentLanguageKey === '__root__') return null
    return decodedSegments[1] || null
  }, [currentLanguageKey, decodedSegments])

  const sectionNode = useMemo(() => {
    if (currentLanguageKey === '__root__') return null
    if (!currentSectionName) return null
    return sections.find((s) => s.name === currentSectionName) || null
  }, [currentLanguageKey, currentSectionName, sections])

  const pages = useMemo(() => {
    if (currentLanguageKey === '__root__') {
      return rootFiles
    }

    if (!sectionNode) return []
    const files: DocsFileNode[] = []
    for (const child of sectionNode.children || []) {
      if (child.type !== 'file') continue
      const file = child as DocsFileNode
      if (file.name.toLowerCase() === 'readme') continue
      files.push(file)
    }
    return files
  }, [currentLanguageKey, rootFiles, sectionNode])

  const selectedNode = useMemo(() => {
    if (!index) return null
    return findNodeByPath(index.root, currentPath)
  }, [index, currentPath])

  const selectedFile = selectedNode?.type === 'file' ? (selectedNode as DocsFileNode) : null

  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([])
      return
    }

    if (searchTimer.current) {
      window.clearTimeout(searchTimer.current)
    }

    searchTimer.current = window.setTimeout(async () => {
      try {
        const res = await searchDocs(searchQuery.trim(), 10)
        const filtered =
          currentLanguageKey === '__root__'
            ? res.results
            : res.results.filter((r) => r.path.startsWith(`${currentLanguageKey}/`))
        setSearchResults(filtered)
      } catch (err) {
        console.error('Search failed:', err)
        setSearchResults([])
      }
    }, 180)

    return () => {
      if (searchTimer.current) {
        window.clearTimeout(searchTimer.current)
      }
    }
  }, [searchQuery, currentLanguageKey])

  const navigateToSegments = useCallback(
    (segments: string[], replace = false) => {
      const encoded = segments.map((s) => encodeURIComponent(s)).join('/')
      const href = encoded ? `/docs/${encoded}` : '/docs'
      if (replace) router.replace(href)
      else router.push(href)
    },
    [router]
  )

  const getDefaultLanguage = useCallback((): DocsDirNode | null => {
    return languages[0] || null
  }, [languages])

  const getDefaultSection = useCallback(
    (language: DocsDirNode | null): DocsDirNode | null => {
      if (!language) return null
      const dirs = (language.children || []).filter((c) => c.type === 'dir') as DocsDirNode[]
      return dirs[0] || null
    },
    []
  )

  const getDefaultPage = useCallback((section: DocsDirNode | null): DocsFileNode | null => {
    if (!section) return null
    const files = (section.children || []).filter((c) => c.type === 'file') as DocsFileNode[]
    const visible = files.filter((f) => f.name.toLowerCase() !== 'readme')
    return visible[0] || null
  }, [])

  // Redirect to a meaningful default route.
  useEffect(() => {
    if (!index || loading || error) return

    // /docs
    if (decodedSegments.length === 0) {
      const defaultLang = getDefaultLanguage()
      if (defaultLang) {
        const defaultSection = getDefaultSection(defaultLang)
        const defaultPage = getDefaultPage(defaultSection)
        if (defaultSection && defaultPage) {
          navigateToSegments([defaultLang.name, defaultSection.name, defaultPage.name], true)
          return
        }
        if (defaultSection) {
          navigateToSegments([defaultLang.name, defaultSection.name], true)
          return
        }
        navigateToSegments([defaultLang.name], true)
        return
      }

      if (rootFiles.length > 0) {
        navigateToSegments([rootFiles[0].name], true)
      }
      return
    }

    // /docs/<language>
    if (languageFromUrl && decodedSegments.length === 1) {
      const lang = languages.find((l) => l.name === languageFromUrl) || null
      const defaultSection = getDefaultSection(lang)
      const defaultPage = getDefaultPage(defaultSection)
      if (defaultSection && defaultPage) {
        navigateToSegments([languageFromUrl, defaultSection.name, defaultPage.name], true)
      }
      return
    }

    // /docs/<language>/<section>
    if (languageFromUrl && decodedSegments.length === 2) {
      const lang = languages.find((l) => l.name === languageFromUrl) || null
      const section = (lang?.children || []).find(
        (c) => c.type === 'dir' && (c as DocsDirNode).name === decodedSegments[1]
      ) as DocsDirNode | undefined
      const defaultPage = getDefaultPage(section || null)
      if (section && defaultPage) {
        navigateToSegments([languageFromUrl, section.name, defaultPage.name], true)
      }
    }
  }, [
    decodedSegments,
    error,
    getDefaultLanguage,
    getDefaultPage,
    getDefaultSection,
    index,
    languageFromUrl,
    languages,
    loading,
    navigateToSegments,
    rootFiles,
  ])

  const getScrollOffset = useCallback(() => {
    const headerHeight = headerRef.current?.getBoundingClientRect().height || 0
    const tabsHeight = tabsRef.current?.getBoundingClientRect().height || 0
    return headerHeight + tabsHeight + 16
  }, [])

  const scrollToHeading = useCallback(
    (id: string, behavior: ScrollBehavior = 'smooth'): boolean => {
      if (!id) return false
      const target = document.getElementById(id)
      if (!target) return false
      const top = target.getBoundingClientRect().top + window.scrollY - getScrollOffset()
      window.scrollTo({ top, behavior })
      return true
    },
    [getScrollOffset]
  )

  const scrollToCurrentHash = useCallback(
    async (behavior: ScrollBehavior = 'auto') => {
      const raw = window.location.hash?.slice(1)
      if (!raw) return
      let id = raw
      try {
        id = decodeURIComponent(raw)
      } catch {
        id = raw
      }

      // Retry a few frames for async markdown rendering.
      for (let i = 0; i < 8; i += 1) {
        if (scrollToHeading(id, behavior)) return
        await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
      }
    },
    [scrollToHeading]
  )

  useEffect(() => {
    const handler = () => {
      scrollToCurrentHash('smooth')
    }
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [scrollToCurrentHash])

  const handleTocSelect = useCallback(
    (id: string) => {
      const encoded = encodeURIComponent(id)
      const url = `${window.location.pathname}${window.location.search}#${encoded}`
      window.history.replaceState(null, '', url)
      scrollToHeading(id, 'smooth')
    },
    [scrollToHeading]
  )

  const languageLabel = useMemo(() => {
    if (currentLanguageKey === '__root__') return 'General'
    return currentLanguageKey
  }, [currentLanguageKey])

  return (
    <div className="relative isolate min-h-screen overflow-hidden bg-[#F6F3EE] text-gray-900">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -top-40 -left-40 h-[520px] w-[520px] rounded-full bg-[radial-gradient(circle_at_center,rgba(143,163,184,0.16),transparent_72%)] blur-3xl" />
        <div className="absolute top-10 -right-52 h-[620px] w-[620px] rounded-full bg-[radial-gradient(circle_at_center,rgba(47,52,55,0.10),transparent_72%)] blur-3xl" />
        <div className="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(255,255,255,0.45),rgba(255,255,255,0.15),rgba(255,255,255,0.5))]" />
      </div>

      {/* Header */}
      <header ref={headerRef} className="sticky top-0 z-50 border-b border-black/10 bg-[#f8f5ef]/90 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
	          <Link href="/projects" className="flex items-center gap-2">
	            <img
	              src={brandLogoSrc}
	              alt="DeepScientist"
	              width={22}
	              height={22}
	              className="object-contain"
	              loading="eager"
	              fetchPriority="high"
	              decoding="async"
	              draggable={false}
	            />
	            <span className="hidden sm:inline text-sm font-semibold">DeepScientist</span>
	          </Link>
          <ChevronRight className="h-4 w-4 text-gray-300" />
          <Link href="/docs" className="text-sm font-medium text-gray-900">
            Docs
          </Link>

          {/* Search */}
          <div className="relative mx-auto hidden w-[560px] max-w-[44vw] md:block">
            <div className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
              <Search className="h-4 w-4 text-gray-400" />
            </div>
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => {
                window.setTimeout(() => setSearchOpen(false), 120)
              }}
              placeholder="Search..."
              className="h-9 rounded-xl border-black/10 bg-white/80 pl-9 pr-14 text-sm shadow-none focus-visible:ring-0"
            />
            <div className="pointer-events-none absolute inset-y-0 right-2 hidden items-center sm:flex">
              <kbd className="rounded-md border bg-white px-1.5 py-0.5 text-[10px] font-mono text-gray-500">
                Ctrl K
              </kbd>
            </div>

            {searchOpen && searchResults.length > 0 ? (
              <div className="absolute left-0 right-0 top-11 z-[10002] overflow-hidden rounded-xl border border-black/10 bg-white/95 shadow-lg">
                <div className="max-h-96 overflow-y-auto py-1">
                  {searchResults.map((r) => (
                    <button
                      key={r.path}
                      type="button"
                      className="flex w-full flex-col gap-0.5 px-3 py-2 text-left hover:bg-gray-50"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        setSearchOpen(false)
                        setSearchQuery('')
                        const segs = r.path.split('/')
                        navigateToSegments(segs, false)
                      }}
                    >
                      <div className="text-sm font-medium text-gray-900">{r.title}</div>
                      <div className="text-xs text-gray-500">{r.path}</div>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          {/* Right: language + user */}
          <div className="ml-auto flex items-center gap-2">
            <NotificationBell size="sm" />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
                  <span>{languageLabel}</span>
                  <ChevronDown className="h-4 w-4 text-gray-400" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem
                  onClick={() => {
                    if (rootFiles.length > 0) navigateToSegments([rootFiles[0].name])
                    else navigateToSegments([])
                  }}
                >
                  General
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {languages.map((lang) => (
                  <DropdownMenuItem
                    key={lang.path}
                    onClick={() => {
                      const defaultSection = getDefaultSection(lang)
                      const defaultPage = getDefaultPage(defaultSection)
                      if (defaultSection && defaultPage) {
                        navigateToSegments([lang.name, defaultSection.name, defaultPage.name])
                        return
                      }
                      if (defaultSection) {
                        navigateToSegments([lang.name, defaultSection.name])
                        return
                      }
                      navigateToSegments([lang.name])
                    }}
                  >
                    {lang.name}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
	            </DropdownMenu>
	          </div>
	        </div>
	      </header>

      <div className="mx-auto max-w-7xl px-4 pt-7">
        <section className="relative overflow-hidden rounded-3xl border border-black/10 bg-white/50 shadow-soft-card backdrop-blur-sm">
          <div
            aria-hidden
            className="absolute inset-0 bg-[linear-gradient(110deg,rgba(122,30,30,0.38),rgba(122,30,30,0.16),rgba(122,30,30,0.30))]"
          />
          <div
            aria-hidden
            className="absolute -inset-24 opacity-25 blur-2xl bg-[conic-gradient(from_180deg_at_50%_50%,rgba(255,122,122,0.5),rgba(255,205,122,0.35),rgba(122,255,227,0.35),rgba(163,122,255,0.3),rgba(255,122,122,0.5))] motion-safe:animate-[spin_26s_linear_infinite]"
          />
          <div className="relative flex flex-col gap-6 px-6 py-8 sm:px-10">
            <div>
              <p className="text-xs uppercase tracking-[0.2em] text-foreground/70">DeepScientist Knowledge Base</p>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
                {selectedFile?.title || 'Docs'}
              </h1>
              <p className="mt-3 max-w-3xl text-sm leading-relaxed text-foreground/75 sm:text-base">
                {currentPath || 'Docs'}
              </p>
            </div>
            <div className="max-w-sm text-sm text-foreground/70">
              <p>Browse by language and section below. Each page stays synced to the current docs tree.</p>
            </div>
          </div>
        </section>
      </div>

      {/* Sections (2nd level) */}
      <div ref={tabsRef} className="mx-auto mt-4 max-w-7xl px-4">
        <div className="overflow-hidden rounded-2xl border border-black/10 bg-white/55 shadow-[0_10px_24px_rgba(15,23,42,0.06)] backdrop-blur-sm">
          <div className="px-4">
            {currentLanguageKey !== '__root__' ? (
              <div className="flex items-center gap-3 overflow-x-auto py-3">
                {sections.map((s) => {
                  const isActive = s.name === currentSectionName
                  return (
                    <button
                      key={s.path}
                      type="button"
                      className={cn(
                        'whitespace-nowrap rounded-full border px-4 py-2 text-sm transition-all',
                        isActive
                          ? 'border-black/20 bg-white text-gray-900 shadow-sm'
                          : 'border-black/10 bg-white/70 text-gray-600 hover:bg-white hover:text-gray-800'
                      )}
                      onClick={() => {
                        const defaultPage = getDefaultPage(s)
                        if (defaultPage) {
                          navigateToSegments([currentLanguageKey, s.name, defaultPage.name])
                        } else {
                          navigateToSegments([currentLanguageKey, s.name])
                        }
                      }}
                    >
                      {s.name}
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="py-3 text-sm text-gray-500">Select a language to browse documentation.</div>
            )}
          </div>
        </div>
      </div>

      {/* Main */}
      <div className="mx-auto max-w-7xl px-4 py-8">
        <div className="flex gap-8">
          {/* Left list (3rd level) */}
          <aside className="hidden w-64 flex-shrink-0 md:block">
            <div className="sticky" style={{ top: getScrollOffset() }}>
              {loading ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
                </div>
              ) : error ? (
                <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">
                  {error}
                </div>
              ) : currentLanguageKey === '__root__' ? (
                <div className="space-y-1">
                  {pages.map((p) => {
                    const active = selectedFile?.path === p.path
                    return (
                      <button
                        key={p.path}
                        type="button"
                        className={cn(
                          'w-full rounded-xl px-3 py-2 text-left text-sm',
                          active
                            ? 'bg-gray-200 text-gray-900 font-medium'
                            : 'text-gray-600 hover:bg-gray-100'
                        )}
                        onClick={() => navigateToSegments([p.name])}
                      >
                        {p.title || p.name}
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="space-y-1">
                  {pages.map((p) => {
                    const active = selectedFile?.path === p.path
                    return (
                      <button
                        key={p.path}
                        type="button"
                        className={cn(
                          'w-full rounded-xl px-3 py-2 text-left text-sm',
                          active
                            ? 'bg-gray-200 text-gray-900 font-medium'
                            : 'text-gray-600 hover:bg-gray-100'
                        )}
                        onClick={() => {
                          if (!currentSectionName) return
                          navigateToSegments([currentLanguageKey, currentSectionName, p.name])
                        }}
                      >
                        {p.title || p.name}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </aside>

          {/* Content */}
          <main className="min-w-0 flex-1">
            {loading ? (
              <div className="flex items-center justify-center py-24">
                <Loader2 className="h-7 w-7 animate-spin text-gray-400" />
              </div>
            ) : error ? (
              <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
                <p className="text-red-600">{error}</p>
              </div>
            ) : !index ? (
              <div className="text-sm text-gray-600">No documentation index.</div>
              ) : selectedFile ? (
              <div className="rounded-2xl border border-black/10 bg-white/80 p-8 shadow-[0_10px_30px_rgba(15,23,42,0.06)] backdrop-blur-sm">
                <DocsContent
                  filePath={selectedFile.file_path}
                  onTocChange={(h) => setToc(h)}
                  onContentReady={() => scrollToCurrentHash('auto')}
                />
              </div>
            ) : (
              <div className="rounded-2xl border border-black/10 bg-white/80 p-8 shadow-[0_10px_30px_rgba(15,23,42,0.06)] backdrop-blur-sm">
                <div className="text-sm text-gray-600">
                  Select a page from the list on the left.
                </div>
              </div>
            )}
          </main>

          {/* TOC */}
          <aside
            className="hidden w-72 flex-shrink-0 xl:block"
            style={{ '--docs-toc-top': `${getScrollOffset()}px` } as CSSProperties}
          >
            {selectedFile ? <DocsToc headings={toc} onSelect={handleTocSelect} /> : null}
          </aside>
        </div>
      </div>
    </div>
  )
}
