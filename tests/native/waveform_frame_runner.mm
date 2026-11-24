// Headless production-kernel/fragment frames. No AppKit, audio or user state.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>
#include "waveform_test_uniforms.h"

struct WaveSample { float height, r, g, b; };
struct Envelope { uint16_t upper, lower; int16_t minimum, maximum; };
struct Clock { uint32_t waveIndex; float wavePhase; uint32_t detailIndex; float detailPhase; };

template<class T> static id<MTLBuffer> buffer(id<MTLDevice> device, const std::vector<T>& values) {
    return [device newBufferWithBytes:values.data() length:values.size()*sizeof(T)
        options:MTLResourceStorageModeShared];
}

static double linear(double value) {
    return value <= .04045 ? value / 12.92 : std::pow((value + .055) / 1.055, 2.4);
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 4) return 2;
        NSError *error = nil;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSString *source = [NSString stringWithContentsOfFile:@(argv[1])
            encoding:NSUTF8StringEncoding error:&error];
        if (!device || !source) return 3;
        id<MTLLibrary> library = [device newLibraryWithSource:source
            options:[MTLCompileOptions new] error:&error];
        if (!library) { fprintf(stderr, "%s\n", error.description.UTF8String); return 4; }
        id<MTLComputePipelineState> compute = [device newComputePipelineStateWithFunction:
            [library newFunctionWithName:@"wave_attribute_kernel"] error:&error];
        MTLRenderPipelineDescriptor *description = [MTLRenderPipelineDescriptor new];
        description.vertexFunction = [library newFunctionWithName:@"wave_surface_vertex"];
        description.fragmentFunction = [library newFunctionWithName:@"wave_surface_fragment"];
        description.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm_sRGB;
        id<MTLRenderPipelineState> render = [device newRenderPipelineStateWithDescriptor:description error:&error];
        if (!compute || !render) { fprintf(stderr, "%s\n", error.description.UTF8String); return 5; }
        NSString *directory = @(argv[2]);
        if (![[NSFileManager defaultManager] createDirectoryAtPath:directory
                withIntermediateDirectories:YES attributes:nil error:&error]) return 6;
        const bool rgbOnly = std::string(argv[3]) == "rgb";
        const bool variedBands = std::string(argv[3]) == "all-variable-bands";
        id<MTLCommandQueue> queue = [device newCommandQueue];
        constexpr uint32_t waveCount = 14400, detailCount = 96000;
        Uniforms shape{};
        shape.samplesPerSecond = 150; shape.sampleCount = waveCount;
        shape.audioDetailSamplesPerSecond = 1000; shape.audioDetailSampleCount = detailCount;
        std::vector<WaveSample> waves[3];
        for (uint32_t i = 0; i < waveCount; ++i) {
            const float height = .42f + .15f * std::sin(i*.071f);
            // Distinct palettes/heights catch accidental use of selected-mode
            // data as the common RGB geometry source.
            waves[0].push_back({height*.75f, .08f, .80f, .86f});
            waves[1].push_back({height, .35f+.2f*std::sin(i*.09f), .35f, .7f});
            if (variedBands) {
                const float low=.28f+.23f*std::sin(i*.037f);
                const float mid=.24f+.21f*std::cos(i*.091f);
                const float high=.20f+.18f*std::sin(i*.153f+.7f);
                const float peak=std::max({low,mid,high});
                waves[2].push_back({peak,low/peak,mid/peak,high/peak});
            } else waves[2].push_back({height*.55f, 1.f, .58f, .21f});
        }
        uint32_t offset = 0, count = waveCount;
        while (count) {
            uint32_t level = shape.waveMipCount++;
            shape.waveMipOffsets[level] = offset; shape.waveMipCounts[level] = count;
            if (count == 1) break;
            for (uint32_t modeIndex=0;modeIndex<3;++modeIndex) {
                auto &stream=waves[modeIndex];
                for (uint32_t i = 0; i < count; i += 2) {
                    const WaveSample a = stream[offset+i];
                    const WaveSample b = stream[offset+std::min(i+1,count-1)];
                    if (variedBands && modeIndex==2) {
                        const float low=std::max(a.r*a.height,b.r*b.height);
                        const float mid=std::max(a.g*a.height,b.g*b.height);
                        const float high=std::max(a.b*a.height,b.b*b.height);
                        const float peak=std::max({low,mid,high});
                        stream.push_back({peak,low/peak,mid/peak,high/peak});
                    } else {
                        const float weight = a.height+b.height;
                        stream.push_back({std::max(a.height,b.height),
                            (a.r*a.height+b.r*b.height)/weight,
                            (a.g*a.height+b.g*b.height)/weight,
                            (a.b*a.height+b.b*b.height)/weight});
                    }
                }
            }
            offset += count; count = (count+1)/2;
        }
        std::vector<Envelope> base;
        for (uint32_t i = 0; i < detailCount; ++i) {
            const float center = .31f*std::sin(i*.019f)+.08f*std::sin(i*.043f);
            const float radius = .015f+.17f*(.5f+.5f*std::sin(i*.013f));
            const int16_t lo = int16_t(std::lround((center-radius)*32767));
            const int16_t hi = int16_t(std::lround((center+radius)*32767));
            base.push_back({uint16_t(std::max(0,int(hi))*2),
                uint16_t(std::max(0,-int(lo))*2),lo,hi});
        }
        std::vector<Envelope> details;
        for (uint32_t divisor : {1u,2u,3u,4u,6u,8u,12u,16u,24u,32u,48u,64u}) {
            const uint32_t level = shape.audioDetailMipCount++;
            shape.audioDetailMipOffsets[level] = uint32_t(details.size());
            shape.audioDetailMipDivisors[level] = divisor;
            for (uint32_t i = 0; i < detailCount; i += divisor) {
                Envelope peak = base[i];
                for (uint32_t j = i+1; j < std::min(i+divisor,detailCount); ++j) {
                    peak.upper = std::max(peak.upper,base[j].upper);
                    peak.lower = std::max(peak.lower,base[j].lower);
                    peak.minimum = std::min(peak.minimum,base[j].minimum);
                    peak.maximum = std::max(peak.maximum,base[j].maximum);
                }
                details.push_back(peak);
            }
            shape.audioDetailMipCounts[level] = (detailCount+divisor-1)/divisor;
        }
        id<MTLBuffer> waveBuffers[3] = {buffer(device,waves[0]),buffer(device,waves[1]),buffer(device,waves[2])};
        id<MTLBuffer> detailBuffer = buffer(device,details);
        NSMutableArray *cases = [NSMutableArray new];
        for (bool audioDetail : {false,true}) for (uint32_t scale : {1u,2u}) for (float zoom : {24.f,32.f,48.f,64.f,92.f,128.f,184.f,256.f,384.f,576.f,864.f,1152.f,1500.f})
        for (float phase : {0.f,.375f,.875f}) for (uint32_t mode : {1u,2u,3u}) {
            if (rgbOnly && mode != 2) continue;
            Uniforms u = shape;
            u.widthPoints = 96; u.heightPoints = 166; u.durationSeconds = 96;
            u.drawableScale = scale; u.drawableWidthPixels = 96*scale;
            u.pixelsPerSecond = zoom; u.waveformMode = mode; u.threeBandActive = mode == 3;
            if (!audioDetail) { u.audioDetailSampleCount = 0; u.audioDetailMipCount = 0; }
            u.waveAttributeCount = u.drawableWidthPixels*4+512;
            u.waveAttributeSamplesPerPixel = 4; u.waveAttributeCurrentOffset = phase; u.waveAttributeValid = 1;
            const double second = 50.123;
            u.waveCurrentIndex = uint32_t(second*150); u.waveCurrentPhase = float(second*150-u.waveCurrentIndex);
            u.audioDetailCurrentIndex = uint32_t(second*1000); u.audioDetailCurrentPhase = float(second*1000-u.audioDetailCurrentIndex);
            std::vector<Clock> clocks;
            const double start = second-double(u.waveAttributeCount/2)/(zoom*scale*4);
            for (uint32_t at = 0; at < u.waveAttributeCount; at += 128) {
                const double t = start+double(at)/(zoom*scale*4);
                const uint32_t wave = uint32_t(t*150), detail = uint32_t(t*1000);
                clocks.push_back({wave,float(t*150-wave),detail,float(t*1000-detail)});
            }
            Uniforms geometry = u; geometry.waveformMode = 2; geometry.threeBandActive = 0;
            id<MTLBuffer> attributes = [device newBufferWithLength:u.waveAttributeCount*32 options:MTLResourceStorageModeShared];
            id<MTLBuffer> blueInner = [device newBufferWithLength:u.waveAttributeCount*16 options:MTLResourceStorageModeShared];
            std::vector<float> emptyPreview(8,0.f);
            id<MTLBuffer> blueOverview = buffer(device,emptyPreview);
            id<MTLBuffer> clockBuffer = buffer(device,clocks);
            MTLTextureDescriptor *textureInfo = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
                MTLPixelFormatBGRA8Unorm_sRGB width:96*scale height:166*scale mipmapped:NO];
            textureInfo.usage = MTLTextureUsageRenderTarget;
            textureInfo.storageMode = MTLStorageModeShared;
            id<MTLTexture> texture = [device newTextureWithDescriptor:textureInfo];
            if (!attributes || !blueInner || !blueOverview || !clockBuffer || !texture) return 7;
            id<MTLCommandBuffer> commands = [queue commandBuffer];
            id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
            [encoder setComputePipelineState:compute];
            [encoder setBuffer:waveBuffers[mode-1] offset:0 atIndex:0];
            [encoder setBytes:&u length:sizeof(u) atIndex:1];
            [encoder setBuffer:detailBuffer offset:0 atIndex:4];
            [encoder setBuffer:attributes offset:0 atIndex:5];
            [encoder setBuffer:clockBuffer offset:0 atIndex:6];
            [encoder setBuffer:waveBuffers[1] offset:0 atIndex:7];
            [encoder setBytes:&geometry length:sizeof(geometry) atIndex:8];
            [encoder setBuffer:blueInner offset:0 atIndex:9];
            [encoder dispatchThreads:MTLSizeMake(u.waveAttributeCount,1,1)
                threadsPerThreadgroup:MTLSizeMake(std::min<NSUInteger>(u.waveAttributeCount,compute.maxTotalThreadsPerThreadgroup),1,1)];
            [encoder endEncoding];
            MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = texture;
            pass.colorAttachments[0].loadAction = MTLLoadActionClear;
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            pass.colorAttachments[0].clearColor = MTLClearColorMake(linear(13./255),linear(14./255),linear(18./255),1);
            id<MTLRenderCommandEncoder> painter = [commands renderCommandEncoderWithDescriptor:pass];
            [painter setRenderPipelineState:render];
            [painter setFragmentBuffer:waveBuffers[mode-1] offset:0 atIndex:0];
            [painter setFragmentBytes:&u length:sizeof(u) atIndex:1];
            [painter setFragmentBuffer:detailBuffer offset:0 atIndex:4];
            [painter setFragmentBuffer:attributes offset:0 atIndex:5];
            [painter setFragmentBuffer:blueOverview offset:0 atIndex:6];
            [painter setFragmentBuffer:blueInner offset:0 atIndex:7];
            [painter setFragmentBuffer:blueOverview offset:0 atIndex:8];
            [painter drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            [painter endEncoding]; [commands commit]; [commands waitUntilCompleted];
            if (commands.status != MTLCommandBufferStatusCompleted) {
                fprintf(stderr,"%s\n",commands.error.description.UTF8String);return 8;
            }
            NSString *name = [NSString stringWithFormat:@"%u-%.0f-%u-%.3f-%u",scale,zoom,mode,phase,audioDetail];
            NSMutableData *pixels = [NSMutableData dataWithLength:96*scale*166*scale*4];
            [texture getBytes:pixels.mutableBytes bytesPerRow:96*scale*4
                fromRegion:MTLRegionMake2D(0,0,96*scale,166*scale) mipmapLevel:0];
            NSData *values = [NSData dataWithBytes:attributes.contents length:attributes.length];
            NSString *pixelName = [name stringByAppendingString:@".bgra"];
            NSString *attributeName = [name stringByAppendingString:@".attributes"];
            if (![pixels writeToFile:[directory stringByAppendingPathComponent:pixelName] atomically:YES]
                    || ![values writeToFile:[directory stringByAppendingPathComponent:attributeName] atomically:YES]) return 9;
            [cases addObject:@{@"mode":@(mode),@"scale":@(scale),@"zoom":@(zoom),@"phase":@(phase),
                @"detail":@(audioDetail),
                @"width":@(96*scale),@"height":@(166*scale),@"attributes":@(u.waveAttributeCount),
                @"pixels":pixelName,@"values":attributeName}];
        }
        NSData *manifest = [NSJSONSerialization dataWithJSONObject:cases options:NSJSONWritingPrettyPrinted error:&error];
        return [manifest writeToFile:[directory stringByAppendingPathComponent:@"cases.json"] atomically:YES] ? 0 : 10;
    }
}
