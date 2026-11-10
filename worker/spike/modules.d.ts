// Wrangler bundles .html as Text modules (see spike/wrangler.jsonc rules).
declare module "*.html" {
	const text: string;
	export default text;
}
