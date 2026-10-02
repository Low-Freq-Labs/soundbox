// Headless test-only Metal execution. No AppKit, audio, AppData or devices.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cstdio>

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 4) return 2;
        NSError *error = nil;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) { fprintf(stderr, "Metal device unavailable\n"); return 3; }
        NSString *source = [NSString stringWithContentsOfFile:@(argv[1])
            encoding:NSUTF8StringEncoding error:&error];
        NSData *input = [NSData dataWithContentsOfFile:@(argv[2])];
        if (!source || !input || !input.length || input.length % (64 * sizeof(float))) return 4;
        MTLCompileOptions *options = [MTLCompileOptions new];
        id<MTLLibrary> library = [device newLibraryWithSource:source options:options error:&error];
        if (!library) { fprintf(stderr, "%s\n", error.description.UTF8String); return 5; }
        id<MTLComputePipelineState> pipeline = [device newComputePipelineStateWithFunction:
            [library newFunctionWithName:@"audit_math"] error:&error];
        if (!pipeline) { fprintf(stderr, "%s\n", error.description.UTF8String); return 6; }
        NSUInteger count = input.length / (64 * sizeof(float));
        id<MTLBuffer> in = [device newBufferWithBytes:input.bytes length:input.length
            options:MTLResourceStorageModeShared];
        id<MTLBuffer> out = [device newBufferWithLength:count * 16 * sizeof(float)
            options:MTLResourceStorageModeShared];
        if (!in || !out) return 7;
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> commands = [queue commandBuffer];
        id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
        [encoder setComputePipelineState:pipeline];
        [encoder setBuffer:in offset:0 atIndex:0];
        [encoder setBuffer:out offset:0 atIndex:1];
        [encoder dispatchThreads:MTLSizeMake(count, 1, 1)
            threadsPerThreadgroup:MTLSizeMake(MIN(count, pipeline.maxTotalThreadsPerThreadgroup), 1, 1)];
        [encoder endEncoding];
        [commands commit];
        [commands waitUntilCompleted];
        if (commands.status != MTLCommandBufferStatusCompleted) {
            fprintf(stderr, "%s\n", commands.error.description.UTF8String); return 8;
        }
        NSData *result = [NSData dataWithBytes:out.contents length:out.length];
        return [result writeToFile:@(argv[3]) options:NSDataWritingAtomic error:&error] ? 0 : 9;
    }
}
