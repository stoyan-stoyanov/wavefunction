import { Container, Footer, Nav } from "@/components/chrome";

export const ensureStatic = "navigation";

export default function BlogLayout({ children }: LayoutProps<"/blog">) {
  return (
    <>
      <Nav />
      <Container className="pb-24 pt-10 sm:pt-16">{children}</Container>
      <Footer />
    </>
  );
}
