import adapter from '@sveltejs/adapter-static';

const base = (process.env.BASE_PATH ?? '').replace(/\/$/, '');
if (base && (!base.startsWith('/') || base.includes('..'))) {
  throw new Error('BASE_PATH must be an absolute URL path, such as /ally-call');
}

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		adapter: adapter(),
		paths: { base }
	}
};

export default config;
