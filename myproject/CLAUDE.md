# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm run dev      # Start development server at http://localhost:3000
npm run build    # Build for production
npm run start    # Start production server
npm run lint     # Run ESLint
```

## Architecture

This is a Next.js 16 app using the App Router, React 19, TypeScript, and Tailwind CSS v4.

- `app/` — App Router directory. `layout.tsx` is the root layout, `page.tsx` is the home page, `globals.css` holds global styles.
- `next.config.ts` — Next.js configuration
- `postcss.config.mjs` — PostCSS config for Tailwind CSS v4
- `eslint.config.mjs` — ESLint flat config (Next.js preset)
