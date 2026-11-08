// Local Vision bridge. No network, credentials, or original-file mutations.
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
int main(int argc, const char *argv[]) {
 @autoreleasepool {
  NSError *error = nil;
  VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
  request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
  request.recognitionLanguages = @[@"zh-Hans", @"zh-Hant", @"en-US"];
  request.usesLanguageCorrection = NO;
  if (argc == 2 && strcmp(argv[1], "--check") == 0) {
   NSArray *languages = [request supportedRecognitionLanguagesAndReturnError:&error];
   if (error || ![languages containsObject:@"zh-Hans"]) return 1;
   puts("vision-zh-v1"); return 0;
  }
  if (argc != 2) return 1;
  NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
  VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithURL:url options:@{}];
  if (![handler performRequests:@[request] error:&error]) { fputs("Local OCR failed\n", stderr); return 1; }
  NSMutableArray *lines = [NSMutableArray array];
  for (VNRecognizedTextObservation *observation in request.results) {
   VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
   if (candidate) [lines addObject:candidate.string];
  }
  NSString *text = [lines componentsJoinedByString:@"\n"];
  if (text.length > 100000) text = [text substringToIndex:100000];
  NSData *output = [NSJSONSerialization dataWithJSONObject:@{@"text":text,@"engine":@"vision-zh-v1"} options:0 error:&error];
  if (!output) return 1;
  [[NSFileHandle fileHandleWithStandardOutput] writeData:output];
 }
 return 0;
}
