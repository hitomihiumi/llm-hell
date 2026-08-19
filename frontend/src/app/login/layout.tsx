import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { getLocale } from "@/i18n/getLocale";
import { LocaleProvider } from "@/i18n/LocaleProvider";

/**
 * The sign-in page sits outside the app shell, so it needs its own locale
 * resolution — and its own switcher. Someone who cannot read the page cannot
 * sign in to reach the one in the header, which makes this the one screen
 * where a missing toggle actually traps a visitor.
 */
export default async function LoginLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await getLocale();

  return (
    <LocaleProvider locale={locale}>
      <div className="absolute top-0 right-0 z-20 p-6 md:p-10">
        <LocaleSwitcher locale={locale} />
      </div>
      {children}
    </LocaleProvider>
  );
}
