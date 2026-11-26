import { Container, Footer, Nav } from "@/components/chrome";
import { DocsSidebar } from "@/components/docs-sidebar";

export const ensureStatic = "navigation";

export default function DocsLayout({ children }: LayoutProps<"/docs">) {
  return (
    <>
      <Nav />
      <Container className="grid gap-x-14 pb-24 pt-8 sm:pt-12 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside>
          <DocsSidebar />
        </aside>
        <main className="min-w-0 max-w-[760px]">{children}</main>
      </Container>
      <Footer />
    </>
  );
}
