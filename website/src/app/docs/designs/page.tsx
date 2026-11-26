import Image from "next/image";
import { DocFooter, DocHeader, H2, P, UI } from "@/components/docs";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/designs",
  "Designs: restyle the whole app",
  "Change how all of Wavefunction looks with a design: pick the built-in Nostalgia or Retro, or ask the agent to make one for you.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Designs"
        lede="A design changes how the whole app looks, with CSS, images and fonts. It never runs code."
        updated="November 26, 2026"
      />
      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        {[
          {
            src: "/wavefunction-design-retro.jpg",
            alt: "The Retro design: pixel type, pixel icons and pixel art of a desk on the new-session screen.",
          },
          {
            src: "/wavefunction-design-nostalgia.jpg",
            alt: "The Nostalgia design: a glass title bar, an Explorer-style sidebar and classic controls.",
          },
          {
            src: "/wavefunction-design-night-light.jpg",
            alt: "Night Light, a design the agent made in a chat: warm charcoal colours and a sleeping moon on the new-session screen.",
          },
        ].map(({ src, alt }, index) => (
          <Image
            key={src}
            src={src}
            alt={alt}
            width={1920}
            height={1440}
            sizes={index === 0 ? "(min-width: 1024px) 760px, 100vw" : "(min-width: 1024px) 375px, 50vw"}
            className={`w-full rounded-[16px] ${index === 0 ? "sm:col-span-2" : ""}`}
          />
        ))}
      </div>

      <H2 id="pick">Pick a design</H2>
      <P>
        Choose one in <UI>Settings › General › Design</UI>. Two are built in: Nostalgia, with a glass
        title bar and a classic navigation pane, and Retro, a dark terminal room with pixel type and
        art.
      </P>

      <H2 id="make">Make your own</H2>
      <P>
        Ask the agent for one in any chat. It writes the design, checks that it loads, and saves it to
        your themes folder for you to pick. You can also put your own designs in the{" "}
        <UI>themes</UI> folder that Settings shows.
      </P>

      <DocFooter path="/docs/designs" />
    </article>
  );
}
