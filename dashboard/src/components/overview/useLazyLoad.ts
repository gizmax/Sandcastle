import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Returns a ref callback to attach to a sentinel element plus a boolean
 * indicating whether that element has ever entered the viewport. Once
 * visible the observer disconnects so the widget stays mounted permanently.
 */
export function useLazyLoad(rootMargin = "500px"): [React.RefCallback<HTMLDivElement>, boolean] {
  const [visible, setVisible] = useState(false);
  const observerRef = useRef<IntersectionObserver | null>(null);

  const setRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
      if (!node || visible) return;

      observerRef.current = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setVisible(true);
            observerRef.current?.disconnect();
            observerRef.current = null;
          }
        },
        { rootMargin },
      );
      observerRef.current.observe(node);
    },
    [rootMargin, visible],
  );

  useEffect(() => {
    return () => {
      observerRef.current?.disconnect();
    };
  }, []);

  return [setRef, visible];
}
