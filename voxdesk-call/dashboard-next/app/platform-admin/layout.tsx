"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import styles from "./platform-admin.module.css";

/**
 * Shell for the platform console.
 *
 * The nav is the map of the surface: one entry per area the Batch 07 API actually
 * serves. A link is only added when its route exists — a dead nav entry trains people
 * to ignore the nav.
 */
const LINKS: Array<{ href: string; label: string }> = [
  { href: "/platform-admin", label: "Overview" },
  { href: "/platform-admin/workforce", label: "Forecast & staffing" },
  { href: "/platform-admin/schedule", label: "Schedules" },
  { href: "/platform-admin/domains", label: "Custom domains" },
  { href: "/platform-admin/branding", label: "Branding" },
  { href: "/platform-admin/email", label: "Sender domains" },
  { href: "/platform-admin/mobile", label: "Mobile push" },
];

export default function PlatformAdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className={styles.shell}>
      <nav className={styles.sidebar} aria-label="Platform admin">
        <div className={styles.brand}>
          VoxDesk
          <small>Platform admin</small>
        </div>
        <ul className={styles.navList}>
          {LINKS.map((link) => {
            const active =
              link.href === "/platform-admin"
                ? pathname === link.href
                : pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className={active ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink}
                  aria-current={active ? "page" : undefined}
                >
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <main className={styles.main}>{children}</main>
    </div>
  );
}
